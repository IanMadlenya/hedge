"""Visualization for global DG functions. Supports VTK, Silo, etc."""

__copyright__ = "Copyright (C) 2007 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


import hedge.tools
import numpy as np

import logging
logger = logging.getLogger(__name__)


class Visualizer(object):
    pass


# {{{ gnuplot mesh vis

def write_gnuplot_mesh(filename, mesh):
    gp_file = open(filename, "w")

    for el in mesh.elements:
        assert el.dimensions == 2
        for pt in el.vertex_indices:
            gp_file.write("%f %f\n" % tuple(mesh.points[pt]))
        gp_file.write("%f %f\n\n" % tuple(mesh.points[el.vertex_indices[0]]))

# }}}


# {{{ legacy vtk

def _three_vector(x):
    if len(x) == 3:
        return x
    elif len(x) == 2:
        return x[0], x[1], 0.
    elif len(x) == 1:
        return x[0], 0, 0.


class LegacyVtkFile(object):
    def __init__(self, pathname, structure, description="Hedge visualization"):
        self.pathname = pathname
        self.structure = structure
        self.description = description

        self.pointdata = []
        self.is_closed = False

    def __fin__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        if not self.is_closed:
            from pyvtk import PointData, VtkData
            vtk = VtkData(self.structure,
                    self.description,
                    PointData(*self.pointdata))
            vtk.tofile(self.pathname)
            self.is_closed = True


class LegacyVtkVisualizer(Visualizer):
    def __init__(self, discr):
        from pyvtk import PolyData

        points = [_three_vector(p) for p in discr.nodes]
        polygons = []

        for eg in discr.element_groups:
            ldis = eg.local_discretization
            for el, (el_start, el_stop) in zip(eg.members, eg.ranges):
                polygons += [[el_start+j for j in element]
                        for element in ldis.get_submesh_indices()]

        self.structure = PolyData(points=points, polygons=polygons)

    def make_file(self, pathname, pcontext=None):
        if pcontext is not None:
            if len(pcontext.ranks) > 1:
                raise RuntimeError("Legacy VTK does not suport "
                        "parallel visualization")
        return LegacyVtkFile(pathname+".vtk", self.structure)

    def add_data(self, vtkfile, scalars=[], vectors=[], scale_factor=1):
        from pyvtk import Scalars, Vectors

        vtkfile.pointdata.extend(
                Scalars(np.array(scale_factor*field),
                    name=name, lookup_table="default")
                for name, field in scalars)
        vtkfile.pointdata.extend(
                Vectors([_three_vector(scale_factor*v)
                    for v in zip(field)], name=name)
                for name, field in vectors)

# }}}


# {{{ xml vtk

class VtkFile(hedge.tools.Closable):
    def __init__(self, pathname, grid, filenames=None, compressor=None):
        """`compressor` may be what ever is accepted by :mod:`pyvisfile`."""
        hedge.tools.Closable.__init__(self)
        self.pathname = pathname
        self.grid = grid
        self.compressor = compressor

    def get_head_pathname(self):
        return self.pathname

    def do_close(self):
        from pytools import assert_not_a_file
        assert_not_a_file(self.pathname)

        outf = file(self.pathname, "w")

        from pyvisfile.vtk import AppendedDataXMLGenerator
        AppendedDataXMLGenerator(self.compressor)(self.grid).write(outf)

        #from pyvisfile.vtk import InlineXMLGenerator
        #InlineXMLGenerator(self.compressor)(self.grid).write(outf)

        outf.close()


class ParallelVtkFile(VtkFile):
    def __init__(self, pathname, grid, index_pathname, pathnames=None,
            compressor=None):
        VtkFile.__init__(self, pathname, grid)
        self.index_pathname = index_pathname
        self.pathnames = pathnames
        self.compressor = compressor

    def get_head_pathname(self):
        return self.index_pathname

    def do_close(self):
        VtkFile.do_close(self)

        from pyvisfile.vtk import ParallelXMLGenerator

        outf = file(self.index_pathname, "w")
        ParallelXMLGenerator(self.pathnames)(self.grid).write(outf)
        outf.close()


class VtkVisualizer(Visualizer, hedge.tools.Closable):
    def __init__(self, discr, pcontext=None, basename=None, compressor=None):
        logger.info("init vtk visualizer: start")

        hedge.tools.Closable.__init__(self)

        from pytools import assert_not_a_file

        if basename is not None:
            self.pvd_name = basename+".pvd"
            assert_not_a_file(self.pvd_name)
        else:
            self.pvd_name = None

        self.pcontext = pcontext
        self.compressor = compressor

        if self.pcontext is None or self.pcontext.is_head_rank:
            self.timestep_to_pathnames = {}
        else:
            self.timestep_to_pathnames = None

        from pyvisfile.vtk import UnstructuredGrid, DataArray, \
                VTK_LINE, VTK_TRIANGLE, VTK_TETRA, VF_LIST_OF_VECTORS
        from hedge.mesh.element import Interval, Triangle, Tetrahedron

        # For now, we use IntVector here because the Python allocator
        # is somewhat reluctant to return allocated chunks of memory
        # to the OS.
        from hedge._internal import IntVector
        cells = IntVector()
        cell_types = IntVector()

        for eg in discr.element_groups:
            ldis = eg.local_discretization
            smi = ldis.get_submesh_indices()

            cells.reserve(len(cells)+len(smi)*len(eg.members))
            for el, el_slice in zip(eg.members, eg.ranges):
                for element in smi:
                    for j in element:
                        cells.append(el_slice.start+j)

            if ldis.geometry is Interval:
                vtk_eltype = VTK_LINE
            elif ldis.geometry is Triangle:
                vtk_eltype = VTK_TRIANGLE
            elif ldis.geometry is Tetrahedron:
                vtk_eltype = VTK_TETRA
            else:
                raise RuntimeError("unsupported element type: %s"
                        % ldis.geometry)

            cell_types.extend([vtk_eltype] * len(smi) * len(eg.members))

        self.grid = UnstructuredGrid(
                (len(discr),
                    DataArray("points", discr.nodes,
                        vector_format=VF_LIST_OF_VECTORS)),
                np.asarray(cells),
                cell_types=np.asarray(cell_types, dtype=np.uint8))

        logger.info("init vtk visualizer: done")

    def update_pvd(self):
        if self.pvd_name and self.timestep_to_pathnames:
            from pyvisfile.vtk import XMLRoot, XMLElement, make_vtkfile

            collection = XMLElement("Collection")

            vtkf = make_vtkfile(collection.tag, compressor=None)
            xmlroot = XMLRoot(vtkf)

            vtkf.add_child(collection)

            from os.path import relpath, dirname
            rel_path_start = dirname(self.pvd_name)

            tsteps = self.timestep_to_pathnames.keys()
            tsteps.sort()
            for i, time in enumerate(tsteps):
                for part, pathname in enumerate(self.timestep_to_pathnames[time]):
                    collection.add_child(XMLElement(
                        "DataSet",
                        timestep=time, part=part,
                        file=relpath(pathname, rel_path_start)))
            outf = open(self.pvd_name, "w")
            xmlroot.write(outf)
            outf.close()

    def do_close(self):
        self.update_pvd()

    def make_file(self, pathname):
        """

        An appropriate extension (including the dot) is automatically
        appended to *pathname*.
        """
        if self.pcontext is None or len(self.pcontext.ranks) == 1:
            return VtkFile(
                    pathname+"."+self.grid.vtk_extension(),
                    self.grid.copy(),
                    compressor=self.compressor
                    )
        else:
            from os.path import basename
            filename_pattern = (
                    pathname + "-%05d." + self.grid.vtk_extension())
            if self.pcontext.is_head_rank:
                return ParallelVtkFile(
                        filename_pattern % self.pcontext.rank,
                        self.grid.copy(),
                        index_pathname="%s.p%s" % (
                            pathname, self.grid.vtk_extension()),
                        pathnames=[
                            basename(filename_pattern % rank)
                            for rank in self.pcontext.ranks],
                        compressor=self.compressor
                        )
            else:
                return VtkFile(
                        filename_pattern % self.pcontext.rank,
                        self.grid.copy(),
                        compressor=self.compressor
                        )

    def register_pathname(self, time, pathname):
        if time is not None and self.timestep_to_pathnames is not None:
            self.timestep_to_pathnames.setdefault(time, []).append(pathname)

            # When we are run under MPI and cancelled by Ctrl+C, destructors
            # do not get called. Therefore, we just spend the (hopefully negligible)
            # time to update the PVD index every few data additions.
            if len(self.timestep_to_pathnames) % 5 == 0:
                self.update_pvd()

    def add_data(self, visf, variables=[], scalars=[], vectors=[],
            time=None, step=None, scale_factor=1):
        if scalars or vectors:
            import warnings
            warnings.warn("`scalars' and `vectors' arguments are deprecated",
                    DeprecationWarning)
            variables = scalars + vectors

        from pyvisfile.vtk import DataArray, VF_LIST_OF_COMPONENTS
        for name, field in variables:
            visf.grid.add_pointdata(DataArray(name, scale_factor*field,
                vector_format=VF_LIST_OF_COMPONENTS))

        self.register_pathname(time, visf.get_head_pathname())

# }}}


# {{{ silo

class SiloMeshData(object):
    def __init__(self, dim, coords, element_groups):
        self.coords = coords

        from pyvisfile.silo import IntVector
        self.ndims = dim
        self.nodelist = IntVector()
        self.shapetypes = IntVector()
        self.shapesizes = IntVector()
        self.shapecounts = IntVector()
        self.nzones = 0

        for nodelist_size_estimate, eg, ldis in element_groups:
            poly_count = 0
            poly_length = None
            self.nodelist.reserve(len(self.nodelist) + nodelist_size_estimate)
            for polygon in eg:
                prev_nodelist_len = len(self.nodelist)
                for i in polygon:
                    self.nodelist.append(int(i))
                poly_count += 1
                poly_length = len(self.nodelist) - prev_nodelist_len

            if poly_count:
                try:
                    from pyvisfile.silo import DB_ZONETYPE_TRIANGLE, DB_ZONETYPE_TET
                except ImportError:
                    pass
                else:
                    from hedge.mesh.element import Triangle, Tetrahedron
                    if ldis.geometry is Triangle:
                        self.shapetypes.append(DB_ZONETYPE_TRIANGLE)
                    elif ldis.geometry is Tetrahedron:
                        self.shapetypes.append(DB_ZONETYPE_TET)
                    else:
                        raise RuntimeError(
                                "unsupported element type: %s" % ldis.geometry)

                self.shapesizes.append(poly_length)
                self.shapecounts.append(poly_count)
                self.nzones += poly_count

    def put_mesh(self, silo, zonelist_name, mesh_name, mesh_opts):
        if self.shapetypes:
            assert len(self.shapetypes) == len(self.shapesizes)
            silo.put_zonelist_2(zonelist_name, self.nzones, self.ndims,
                    self.nodelist, 0, 0, self.shapetypes, self.shapesizes,
                    self.shapecounts)
        else:
            silo.put_zonelist(zonelist_name, self.nzones, self.ndims, self.nodelist,
                    self.shapesizes, self.shapecounts)

        silo.put_ucdmesh(mesh_name, [], self.coords, self.nzones,
                zonelist_name, None, mesh_opts)


class SiloVisualizer(Visualizer):
    def __init__(self, discr, pcontext=None):
        self.discr = discr
        self.pcontext = pcontext

        self.generated = False

    def _generate(self):
        logger.info("generating data for silo visualizer: start")

        # only generate vis data when vis is really needed.
        # saves startup time when debugging.
        def generate_fine_elements(eg):
            ldis = eg.local_discretization
            smi = ldis.get_submesh_indices()
            for el, el_slice in zip(eg.members, eg.ranges):
                for element in smi:
                    yield [el_slice.start+j for j in element]

        def generate_fine_element_groups():
            for eg in discr.element_groups:
                ldis = eg.local_discretization
                smi = ldis.get_submesh_indices()
                nodelist_size_estimate = len(eg.members) * len(smi) * len(smi[0])
                yield nodelist_size_estimate, generate_fine_elements(eg), ldis

        def generate_coarse_elements(eg):
            for el in eg.members:
                yield el.vertex_indices

        def generate_coarse_element_groups():
            for eg in discr.element_groups:
                if eg.members:
                    nodelist_size_estimate = len(eg.members) \
                            * len(eg.members[0].vertex_indices)
                else:
                    nodelist_size_estimate = 0

                yield (nodelist_size_estimate, generate_coarse_elements(eg),
                        eg.local_discretization)

        discr = self.discr
        self.dim = discr.dimensions
        if self.dim != 1:
            self.fine_mesh = SiloMeshData(self.dim,
                    np.asarray(discr.nodes.T, order="C"),
                    generate_fine_element_groups())
            self.coarse_mesh = SiloMeshData(self.dim,
                    np.asarray(discr.mesh.points.T, order="C"),
                    generate_coarse_element_groups())
        else:
            self.xvals = np.asarray(discr.nodes.T, order="C")

        logger.info("generating data for silo visualizer: done")

        self.generated = True

    def close(self):
        pass

    def make_file(self, pathname):
        """This function returns either a :class:`pyvisfile.silo.SiloFile` or a
        :class:`pyvisfile.silo.ParallelSiloFile`, depending on the ParallelContext
        under which we are running

        An extension of .silo is automatically appended to *pathname*.
        """
        if not self.generated:
            self._generate()

        if self.pcontext is None or len(self.pcontext.ranks) == 1:
            from pyvisfile.silo import SiloFile
            return SiloFile(pathname+".silo")
        else:
            from pyvisfile.silo import ParallelSiloFile
            return ParallelSiloFile(
                    pathname,
                    self.pcontext.rank, self.pcontext.ranks)

    def add_data(self, silo, variables=[], scalars=[], vectors=[], expressions=[],
            time=None, step=None, scale_factor=1):
        if scalars or vectors:
            import warnings
            warnings.warn("`scalars' and `vectors' arguments are deprecated",
                    DeprecationWarning)
            variables = scalars + vectors

        from pyvisfile.silo import DB_NODECENT, DBOPT_DTIME, DBOPT_CYCLE

        # put mesh coordinates
        mesh_opts = {}
        if time is not None:
            mesh_opts[DBOPT_DTIME] = float(time)
        if step is not None:
            mesh_opts[DBOPT_CYCLE] = int(step)

        if self.dim == 1:
            for name, field in variables:
                from hedge.tools import is_obj_array
                if is_obj_array(field):
                    AXES = ["x", "y", "z", "w"]
                    for i, f_i in enumerate(field):
                        silo.put_curve(name+AXES[i], self.xvals,
                                scale_factor*f_i, mesh_opts)
                else:
                    silo.put_curve(name, self.xvals,
                            scale_factor*field, mesh_opts)
        else:
            self.fine_mesh.put_mesh(silo, "finezonelist", "finemesh", mesh_opts)
            self.coarse_mesh.put_mesh(silo, "coarsezonelist", "mesh", mesh_opts)

            from hedge.tools import log_shape

            # put data
            for name, field in variables:
                ls = log_shape(field)
                if ls != () and ls[0] > 1:
                    assert len(ls) == 1
                    silo.put_ucdvar(name, "finemesh",
                            ["%s_comp%d" % (name, i)
                                for i in range(ls[0])],
                            scale_factor*field, DB_NODECENT)
                else:
                    if ls != ():
                        field = field[0]
                    silo.put_ucdvar1(
                            name, "finemesh", scale_factor*field, DB_NODECENT)

        if expressions:
            silo.put_defvars("defvars", expressions)

# }}}


# {{{ tools

def get_rank_partition(pcon, discr):
    vec = discr.volume_zeros()
    vec[:] = pcon.rank
    return vec

# }}}

# vim: foldmethod=marker
