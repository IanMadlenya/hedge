# Hedge - the Hybrid'n'Easy DG Environment
# Copyright (C) 2007 Andreas Kloeckner
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.




import pylinear.array as num




VTK_INT8 = "Int8"
VTK_UINT8 = "UInt8"
VTK_INT16 = "Int16"
VTK_UINT16 = "UInt16"
VTK_INT32 = "Int32"
VTK_UINT32 = "UInt32"
VTK_INT64 = "Int64"
VTK_UINT64 = "UInt64"
VTK_FLOAT32 = "Float32"
VTK_FLOAT64 = "Float64"

VTK_VERTEX = 1
VTK_POLY_VERTEX = 2
VTK_LINE = 3
VTK_POLY_LINE = 4
VTK_TRIANGLE = 5
VTK_TRIANGLE_STRIP = 6
VTK_POLYGON = 7
VTK_PIXEL = 8
VTK_QUAD = 9
VTK_TETRA = 10
VTK_VOXEL = 11
VTK_HEXAHEDRON = 12
VTK_WEDGE = 13
VTK_PYRAMID = 14




# Ah, the joys of home-baked non-compliant XML goodness.
class XMLElementBase:
    def __init__(self):
        self.children = []

    def copy(self, new_children=None):
        result = self.__class__(self.tag, self.attributes)
        if new_children is not None:
            result.children = new_children
        else:
            result.children = self.children
        return result

    def add_child(self, child):
        self.children.append(child)




class XMLElement(XMLElementBase):
    def __init__(self, tag, **attributes):
        XMLElementBase.__init__(self)
        self.tag = tag
        self.attributes = attributes

    def write(self, file):
        attr_string = "".join(
                " %s=\"%s\"" % (key,value) 
                for key,value in self.attributes.iteritems())
        if self.children:
            file.write("<%s%s>\n" % (self.tag, attr_string))
            for child in self.children:
                if isinstance(child, XMLElement):
                    child.write(file)
                else:
                    # likely a string instance, write it directly
                    file.write(child)
            file.write("</%s>\n" % self.tag)
        else:
            file.write("<%s%s/>\n" % (self.tag, attr_string))





class XMLRoot(XMLElementBase):
    def __init__(self, child=None):
        XMLElementBase.__init__(self)
        if child:
            self.add_child(child)

    def write(self, file):
        file.write("<?xml version=\"1.0\"?>\n")
        for child in self.children:
            if isinstance(child, XMLElement):
                child.write(file)
            else:
                # likely a string instance, write it directly
                file.write(child)



class DataArray:
    def __init__(self, name, container, typehint=None, vec_padding=3, list_of_components=True):
        self.name = name

        if isinstance(container, DataArray):
            self.type = container.type
            self.components = container.components
            self.buffer = container.buffer
            self.encoding_cache = container.encoding_cache
            return

        from hedge._internal import \
                bufferize_vector, \
                bufferize_list_of_vectors, \
                bufferize_list_of_components, \
                bufferize_int32, \
                bufferize_uint8

        if num.Vector.is_a(container):
            self.type = "Float64"
            self.components = 1
            self.buffer = bufferize_vector(container)
        elif isinstance(container, list):
            if len(container) == 0 or not num.Vector.is_a(container[0]):
                self.components = 1
                if typehint == VTK_UINT8:
                    self.type = VTK_UINT8
                    self.buffer = bufferize_uint8(container)
                else:
                    self.type = VTK_INT32
                    self.buffer = bufferize_int32(container)
            else:
                self.type = "Float64"
                if list_of_components:
                    ctr = list(container)
                    while len(ctr) < vec_padding:
                        ctr.append(None)
                    self.components = len(ctr)
                    self.buffer =  bufferize_list_of_components(ctr, len(ctr[0]))
                else:
                    self.components = len(container[0])
                    if self.components < vec_padding:
                        self.components = vec_padding
                    self.buffer =  bufferize_list_of_vectors(container, self.components)
        else:
            raise ValueError, "cannot convert object of type `%s' to DataArray" % container

        self.encoding_cache = {}

    def encode(self, compressor, xml_element):
        from hedge._internal import bufferize_int32
        from base64 import b64encode

        try:
            b64header, b64data = self.encoding_cache[compressor]
        except KeyError:
            if compressor == "zlib":
                from zlib import compress
                comp_buffer = compress(self.buffer)
                comp_header = [1, len(self.buffer), len(self.buffer), len(comp_buffer)]
                b64header = b64encode(bufferize_int32(comp_header))
                b64data = b64encode(comp_buffer)
            else:
                b64header = b64encode( bufferize_int32([len(self.buffer)]))
                b64data = b64encode(self.buffer)
                
            self.encoding_cache[compressor] = b64header, b64data

        xml_element.add_child(b64header)
        xml_element.add_child(b64data)

        return len(b64header) + len(b64data)

    def invoke_visitor(self, visitor):
        return visitor.gen_data_array(self)




class UnstructuredGrid:
    def __init__(self, points, cells, cell_types):
        self.point_count = len(points)
        self.cell_count = len(cells)

        try:
            self.point_count, self.points = points
        except:
            self.point_count = len(points)
            self.points = DataArray("points", points, list_of_components=False)

        try:
            self.cell_count, self.cell_connectivity, \
                    self.cell_offsets = cells
        except:
            self.cell_count = len(cells)

            connectivity = []
            offsets = []

            for cell in cells:
                connectivity.extend(cell)
                offsets.append(len(connectivity))

            self.cell_connectivity = DataArray("connectivity", connectivity)
            self.cell_offsets = DataArray("offsets", offsets)

        self.cell_types = DataArray("types", cell_types, VTK_UINT8)

        self.pointdata = []
        self.celldata = []

    def copy(self):
        return UnstructuredGrid(
                (self.point_count, self.points),
                (self.cell_count, self.cell_connectivity,
                    self.cell_offsets), 
                self.cell_types)

    def vtk_extension(self):
        return "vtu"

    def invoke_visitor(self, visitor):
        return visitor.gen_unstructured_grid(self)

    def add_pointdata(self, name, data):
        self.pointdata.append(DataArray(name, data))





def make_vtkfile(filetype, compressor):
    import sys
    if sys.byteorder == "little":
        bo = "LittleEndian"
    else:
        bo = "BigEndian"

    kwargs = {}
    if compressor == "zlib":
        kwargs["compressor"] = "vtkZLibDataCompressor"

    return XMLElement("VTKFile", type=filetype, version="0.1", byte_order=bo, **kwargs)




class XMLGenerator:
    def __init__(self, compressor):
        if compressor == "zlib":
            try:
                import zlib
            except ImportError:
                compress = False
        elif compressor is None:
            pass
        else:
            raise ValueError, "Invalid compressor name `%s'" % compressor

        self.compressor = compressor

    def __call__(self, vtkobj):
        child = self.rec(vtkobj)
        vtkf = make_vtkfile(child.tag, self.compressor)
        vtkf.add_child(child)
        return XMLRoot(vtkf)

    def rec(self, vtkobj):
        return vtkobj.invoke_visitor(self)





class InlineXMLGenerator(XMLGenerator):
    def gen_unstructured_grid(self, ugrid):
        el = XMLElement("UnstructuredGrid")
        piece = XMLElement("Piece", 
                NumberOfPoints=ugrid.point_count, NumberOfCells=ugrid.cell_count)
        el.add_child(piece)

        pointdata = XMLElement("PointData")
        piece.add_child(pointdata)
        for data_array in ugrid.pointdata:
            pointdata.add_child(self.rec(data_array))

        points = XMLElement("Points")
        piece.add_child(points)
        points.add_child(self.rec(ugrid.points))

        cells = XMLElement("Cells")
        piece.add_child(cells)
        cells.add_child(self.rec(ugrid.cell_connectivity))
        cells.add_child(self.rec(ugrid.cell_offsets))
        cells.add_child(self.rec(ugrid.cell_types))

        return el

    def gen_data_array(self, data):
        el = XMLElement("DataArray", type=data.type, Name=data.name, 
                NumberOfComponents=data.components, format="binary")
        data.encode_buffer(self.compressor, el)
        el.add_child("\n")
        return el




class AppendedDataXMLGenerator(InlineXMLGenerator):
    def __init__(self, compressor):
        InlineXMLGenerator.__init__(self, compressor)

        self.base64_len = 0
        self.app_data = XMLElement("AppendedData", encoding="base64")
        self.app_data.add_child("_")

    def __call__(self, vtkobj):
        xmlroot = XMLGenerator.__call__(self, vtkobj)
        self.app_data.add_child("\n")
        xmlroot.children[0].add_child(self.app_data)
        return xmlroot

    def gen_data_array(self, data):
        el = XMLElement("DataArray", type=data.type, Name=data.name, 
                NumberOfComponents=data.components, format="appended", 
                offset=self.base64_len)

        self.base64_len += data.encode(self.compressor, self.app_data)

        return el




class ParallelXMLGenerator(XMLGenerator):
    def __init__(self, pathnames):
        XMLGenerator.__init__(self, compressor=None)

        self.pathnames = pathnames

    def gen_unstructured_grid(self, ugrid):
        el = XMLElement("PUnstructuredGrid")

        pointdata = XMLElement("PPointData")
        el.add_child(pointdata)
        for data_array in ugrid.pointdata:
            pointdata.add_child(self.rec(data_array))

        points = XMLElement("PPoints")
        el.add_child(points)
        points.add_child(self.rec(ugrid.points))

        cells = XMLElement("PCells")
        el.add_child(cells)
        cells.add_child(self.rec(ugrid.cell_connectivity))
        cells.add_child(self.rec(ugrid.cell_offsets))
        cells.add_child(self.rec(ugrid.cell_types))

        for pn in self.pathnames:
            el.add_child(XMLElement("Piece", Source=pn))

        return el

    def gen_data_array(self, data):
        from hedge._internal import bufferize_int32
        el = XMLElement("PDataArray", type=data.type, Name=data.name, 
                NumberOfComponents=data.components)
        return el





