"""Canned operators for several PDEs, such as Maxwell's, heat, Poisson, etc."""

from __future__ import division

__copyright__ = "Copyright (C) 2007 Andreas Kloeckner"

__license__ = """
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see U{http://www.gnu.org/licenses/}.
"""





import pylinear.array as num
import pylinear.computation as comp
import hedge.tools
import hedge.mesh
import hedge.data




class Operator(object):
    """A base class for Discontinuous Galerkin operators.

    You may derive your own operators from this class, but, at present
    this class provides no functionality. Its function is merely as 
    documentation, to group related classes together in an inheritance
    tree.
    """
    pass




class TimeDependentOperator(Operator):
    """A base class for time-dependent Discontinuous Galerkin operators.

    You may derive your own operators from this class, but, at present
    this class provides no functionality. Its function is merely as 
    documentation, to group related classes together in an inheritance
    tree.
    """
    pass




class GradientOperator(Operator):
    def __init__(self, discr):
        self.discr = discr

        from hedge.flux import make_normal, FluxScalarPlaceholder
        u = FluxScalarPlaceholder()

        self.nabla = discr.nabla
        self.m_inv = discr.inverse_mass_operator
        normal = make_normal(self.discr.dimensions)
        self.flux = discr.get_flux_operator(u.int*normal - u.avg*normal)

    def __call__(self, u):
        from hedge.mesh import TAG_ALL
        from hedge.discretization import pair_with_boundary

        bc = self.discr.boundarize_volume_field(u, TAG_ALL)

        return self.nabla*u - self.m_inv*(
                self.flux * u + 
                self.flux * pair_with_boundary(u, bc, TAG_ALL))




class DivergenceOperator(Operator):
    def __init__(self, discr):
        self.discr = discr

        from hedge.flux import make_normal, FluxVectorPlaceholder
        v = FluxVectorPlaceholder(discr.dimensions)

        self.nabla = discr.nabla
        self.m_inv = discr.inverse_mass_operator
        normal = make_normal(self.discr.dimensions)

        from hedge.tools import dot
        self.flux = discr.get_flux_operator(dot(v.int-v.avg, normal))

    def __call__(self, v):
        from hedge.mesh import TAG_ALL
        from hedge.discretization import pair_with_boundary
        from hedge.tools import dot

        bc = self.discr.boundarize_volume_field(v, TAG_ALL)

        return dot(self.nabla, v) - self.m_inv*(
                self.flux * v + 
                self.flux * pair_with_boundary(v, bc, TAG_ALL))




class AdvectionOperatorBase(TimeDependentOperator):
    def __init__(self, discr, v, 
            inflow_tag="inflow",
            inflow_u=hedge.data.make_tdep_constant(0),
            outflow_tag="outflow",
            flux_type="central"):
        self.discr = discr
        self.v = v
        self.inflow_tag = inflow_tag
        self.inflow_u = inflow_u
        self.outflow_tag = outflow_tag
        self.flux_type = flux_type

        from hedge.mesh import check_bc_coverage
        check_bc_coverage(discr.mesh, [inflow_tag, outflow_tag])

        self.nabla = discr.nabla
        self.mass = discr.mass_operator
        self.m_inv = discr.inverse_mass_operator
        self.minv_st = discr.minv_stiffness_t

        self.flux = discr.get_flux_operator(self.get_flux())

    flux_types = [
            "central",
            #"upwind",
            "lf"
            ]

    def get_weak_flux(self):
        from hedge.flux import make_normal, FluxScalarPlaceholder
        from hedge.tools import dot

        u = FluxScalarPlaceholder(0)
        normal = make_normal(self.discr.dimensions)

        if self.flux_type == "central":
            return u.avg*dot(normal, self.v)
        elif self.flux_type in ["lf", "upwind"]:
            return u.avg*dot(normal, self.v) \
                    - 0.5*comp.norm_2(self.v)*(u.int - u.ext)
        else:
            raise ValueError, "invalid flux type"


class StrongAdvectionOperator(AdvectionOperatorBase):
    def get_flux(self):
        from hedge.flux import make_normal, FluxScalarPlaceholder
        from hedge.tools import dot

        u = FluxScalarPlaceholder(0)
        normal = make_normal(self.discr.dimensions)

        return u.int * dot(normal, self.v) - self.get_weak_flux()

    def rhs(self, t, u):
        from hedge.discretization import pair_with_boundary
        from hedge.tools import dot

        bc_in = self.inflow_u.boundary_interpolant(t, self.discr, self.inflow_tag)

        return dot(self.v, self.nabla*u) - self.m_inv*(
                self.flux * u + 
                self.flux * pair_with_boundary(u, bc_in, self.inflow_tag))




class WeakAdvectionOperator(AdvectionOperatorBase):
    def get_flux(self):
        return self.get_weak_flux()

    def rhs(self, t, u):
        from hedge.discretization import pair_with_boundary
        from hedge.tools import dot

        bc_in = self.inflow_u.boundary_interpolant(t, self.discr, self.inflow_tag)
        bc_out = self.discr.boundarize_volume_field(u, self.outflow_tag)

        return -dot(self.v, self.minv_st*u) + self.m_inv*(
                self.flux*u
                + self.flux * pair_with_boundary(u, bc_in, self.inflow_tag)
                + self.flux * pair_with_boundary(u, bc_out, self.outflow_tag)
                )




class MaxwellOperator(TimeDependentOperator):
    """A 3D Maxwell operator with PEC boundaries.

    Field order is [Ex Ey Ez Hx Hy Hz].
    """

    def __init__(self, discr, epsilon, mu, upwind_alpha=1, 
            pec_tag=hedge.mesh.TAG_ALL, direct_flux=True,
            current=None):
        from hedge.flux import make_normal, FluxVectorPlaceholder
        from hedge.mesh import check_bc_coverage
        from hedge.discretization import pair_with_boundary
        from math import sqrt
        from pytools.arithmetic_container import join_fields
        from hedge.tools import SubsettableCrossProduct

        e_subset = self.get_subset()[0:3]
        h_subset = self.get_subset()[3:6]

        e_cross = self.e_cross = SubsettableCrossProduct(
                op2_subset=e_subset, result_subset=h_subset)
        h_cross = self.h_cross = SubsettableCrossProduct(
                op2_subset=h_subset, result_subset=e_subset)

        self.discr = discr

        self.epsilon = epsilon
        self.mu = mu
        self.c = 1/sqrt(mu*epsilon)

        self.pec_tag = pec_tag

        self.current = current

        check_bc_coverage(discr.mesh, [pec_tag])

        dim = discr.dimensions
        normal = make_normal(dim)

        w = FluxVectorPlaceholder(self.component_count())
        e, h = self.split_fields(w)

        Z = sqrt(mu/epsilon)
        Y = 1/Z

        fluxes = join_fields(
                # flux e
                1/epsilon*(
                    1/2*h_cross(normal, h.int-h.ext)
                    -upwind_alpha/(2*Z)*h_cross(normal, e_cross(normal, e.int-e.ext))
                    ),
                # flux h
                1/mu*(
                    -1/2*e_cross(normal, e.int-e.ext)
                    -upwind_alpha/(2*Y)*e_cross(normal, h_cross(normal, h.int-h.ext))
                    ),
                )

        self.flux = discr.get_flux_operator(fluxes, direct=direct_flux)

        self.nabla = discr.nabla
        self.m_inv = discr.inverse_mass_operator

    def rhs(self, t, w):
        from hedge.tools import cross
        from hedge.discretization import pair_with_boundary
        from pytools.arithmetic_container import join_fields, ArithmeticList

        e, h = self.split_fields(w)

        def e_curl(field):
            return self.e_cross(self.nabla, field)

        def h_curl(field):
            return self.h_cross(self.nabla, field)

        bc = join_fields(
                -self.discr.boundarize_volume_field(e, self.pec_tag),
                self.discr.boundarize_volume_field(h, self.pec_tag)
                )

        bpair = pair_with_boundary(w, bc, self.pec_tag)

        local_op_fields = join_fields(
                1/self.epsilon * h_curl(h),
                - 1/self.mu * e_curl(e),
                )

        if self.current is not None:
            j = self.current.volume_interpolant(t, self.discr)
            e_idx = 0 
            for j_idx, use_component in enumerate(self.get_subset()[0:3]):
                if use_component:
                    local_op_fields[e_idx] -= j[j_idx]
                    e_idx += 1
            
        return local_op_fields - self.m_inv*(
                    self.flux * w
                    +self.flux * pair_with_boundary(w, bc, self.pec_tag)
                    )

    def split_fields(self, w):
        e_subset = self.get_subset()[0:3]
        h_subset = self.get_subset()[3:6]

        idx = 0

        e = []
        for use_component in e_subset:
            if use_component:
                e.append(w[idx])
                idx += 1

        h = []
        for use_component in h_subset:
            if use_component:
                h.append(w[idx])
                idx += 1

        from hedge.flux import FluxVectorPlaceholder
        from pytools.arithmetic_container import ArithmeticList

        if isinstance(w, FluxVectorPlaceholder):
            return FluxVectorPlaceholder(scalars=e), FluxVectorPlaceholder(scalars=h)
        elif isinstance(w, ArithmeticList):
            return ArithmeticList(e), ArithmeticList(h)
        else:
            return e, h

    def component_count(self):
        from pytools import len_iterable
        return len_iterable(uc for uc in self.get_subset() if uc)

    def get_subset(self):
        """Return a 6-tuple of C{bool}s indicating whether field components 
        are to be computed. The fields are numbered in the order specified
        in the class documentation.
        """
        return 6*(True,)




class TMMaxwellOperator(MaxwellOperator):
    """A 2D TM Maxwell operator with PEC boundaries.

    Field order is [Ez Hx Hy].
    """

    def get_subset(self):
        return (
                (False,False,True) # only ez
                +
                (True,True,False) # hx and hy
                )




class TEMaxwellOperator(MaxwellOperator):
    """A 2D TE Maxwell operator with PEC boundaries.

    Field order is [Ex Ey Hz].
    """

    def get_subset(self):
        return (
                (True,True,False) # ex and ey
                +
                (False,False,True) # only hz
                )




class WeakPoissonOperator(Operator,hedge.tools.PylinearOperator):
    """Implements the Local Discontinuous Galerkin (LDG) Method for elliptic
    operators.

    See P. Castillo et al., 
    Local discontinuous Galerkin methods for elliptic problems", 
    Communications in Numerical Methods in Engineering 18, no. 1 (2002): 69-75.
    """
    def __init__(self, discr, diffusion_tensor=None, 
            dirichlet_bc=hedge.data.ConstantGivenFunction(), dirichlet_tag="dirichlet",
            neumann_bc=hedge.data.ConstantGivenFunction(), neumann_tag="neumann",
            ldg=True):
        hedge.tools.PylinearOperator.__init__(self)

        self.discr = discr

        fs = self.get_weak_flux_set(ldg)

        self.flux_u = discr.get_flux_operator(fs.flux_u)
        self.flux_v = discr.get_flux_operator(fs.flux_v)
        self.flux_u_dbdry = discr.get_flux_operator(fs.flux_u_dbdry)
        self.flux_v_dbdry = discr.get_flux_operator(fs.flux_v_dbdry)
        self.flux_u_nbdry = discr.get_flux_operator(fs.flux_u_nbdry)
        self.flux_v_nbdry = discr.get_flux_operator(fs.flux_v_nbdry)

        self.stiff_t = discr.stiffness_t_operator
        self.mass = discr.mass_operator
        self.m_inv = discr.inverse_mass_operator

        from math import sqrt
        from hedge.mesh import check_bc_coverage

        check_bc_coverage(discr.mesh, [dirichlet_tag, neumann_tag])

        # treat diffusion tensor
        if diffusion_tensor is None:
            diffusion_tensor = hedge.data.ConstantGivenFunction(
                    num.identity(discr.dimensions))

        from pytools.arithmetic_container import ArithmeticListMatrix
        if isinstance(diffusion_tensor, hedge.data.ConstantGivenFunction):
            self.diffusion = self.neu_diff = \
                    ArithmeticListMatrix(diffusion_tensor.value)
        else:
            def fast_diagonal_mat(vec):
                return num.diagonal_matrix(vec, flavor=num.SparseExecuteMatrix)
            self.diffusion = diffusion_tensor.volume_interpolant(discr).map(
                    fast_diagonal_mat)
            self.neu_diff = diffusion_tensor.boundary_interpolant(discr, neumann_tag).map(
                    fast_diagonal_mat)

        self.dirichlet_bc = dirichlet_bc
        self.dirichlet_tag = dirichlet_tag
        self.neumann_bc = neumann_bc
        self.neumann_tag = neumann_tag

        self.neumann_normals = discr.boundary_normals(self.neumann_tag)

    # pylinear operator infrastructure ----------------------------------------
    def size1(self):
        return len(self.discr)

    def size2(self):
        return len(self.discr)

    def apply(self, before, after):
        after[:] = self.op(before)

    # fluxes ------------------------------------------------------------------
    def get_weak_flux_set(self, ldg):
        class FluxSet: pass
        fs = FluxSet()

        from hedge.flux import \
                FluxVectorPlaceholder, FluxScalarPlaceholder, \
                make_normal, PenaltyTerm
        from hedge.tools import dot

        dim = self.discr.dimensions
        vec = FluxVectorPlaceholder(1+dim)
        fs.u = u = vec[0]
        fs.v = v = vec[1:]
        normal = make_normal(dim)

        # central flux
        fs.flux_u = u.avg*normal
        fs.flux_v = dot(v.avg, normal)

        # ldg terms
        from pytools.arithmetic_container import ArithmeticList 
        ldg_beta = ArithmeticList([1]*dim)

        fs.flux_u = fs.flux_u - (u.int-u.ext)*0.5*ldg_beta
        fs.flux_v = fs.flux_v + dot((v.int-v.ext)*0.5, ldg_beta)

        # penalty term
        stab_term = PenaltyTerm() * (u.int - u.ext)
        fs.flux_v -= stab_term

        # boundary fluxes
        fs.flux_u_dbdry = normal * u.ext
        fs.flux_v_dbdry = dot(v.int, normal) - stab_term

        fs.flux_u_nbdry = normal * u.int
        fs.flux_v_nbdry = dot(normal, v.ext)

        return fs

    # operator application, rhs prep ------------------------------------------
    def grad(self, u):
        from hedge.discretization import pair_with_boundary

        return self.m_inv * (
                - (self.stiff_t * u)
                + self.flux_u*u
                + self.flux_u_dbdry*pair_with_boundary(u, 0, self.dirichlet_tag)
                + self.flux_u_nbdry*pair_with_boundary(u, 0, self.neumann_tag)
                )

    def div(self, v, u=None, apply_minv=True):
        """Compute the divergence of v using an LDG operator.

        The divergence computation is unaffected by the scaling
        effected by the diffusion tensor.

        @param apply_minv: Bool specifying whether to compute a complete 
          divergence operator. If False, the final application of the inverse
          mass operator is skipped. This is used in L{op}() in order to reduce
          the scheme M{M^{-1} S u = f} to M{S u = M f}, so that the mass operator
          only needs to be applied once, when preparing the right hand side
          in @L{prepare_minv}.
        """
        from hedge.discretization import pair_with_boundary
        from hedge.tools import dot
        from pytools.arithmetic_container import join_fields

        dim = self.discr.dimensions

        if u is None:
            u = self.discr.volume_zeros()
        w = join_fields(u, v)

        dirichlet_bc_w = join_fields(0, [0]*dim)
        neumann_bc_w = join_fields(0, [0]*dim)

        result = (
                -dot(self.stiff_t, v)
                + self.flux_v * w
                + self.flux_v_dbdry * pair_with_boundary(w, dirichlet_bc_w, self.dirichlet_tag)
                + self.flux_v_nbdry * pair_with_boundary(w, neumann_bc_w, self.neumann_tag)
                )
        if apply_minv:
            return self.m_inv * result
        else:
            return result

    def op(self, u):
        return self.div(self.diffusion * self.grad(u), u, apply_minv=False)

    def prepare_rhs(self, rhs):
        """Perform the rhs(*) function in the class description, i.e.
        return a right hand side for the linear system op(u)=rhs(f).
        
        In matrix form, LDG looks like this:
        
        Mv = Cu + g
        Mf = Av + Bu + h

        where v is the auxiliary vector, u is the argument of the operator, f
        is the result of the operator and g and h are inhom boundary data, and
        A,B,C are some operator+lifting matrices

        M f = A Minv(Cu + g) + Bu + h

        so the linear system looks like

        M f = A Minv Cu + A Minv g + Bu + h
        M f - A Minv g - h = (A Minv C + B)u

        So the right hand side we're putting together here is really

        M f - A Minv g - h
        """

        from pytools.arithmetic_container import ArithmeticList 
        from hedge.discretization import pair_with_boundary
        from hedge.tools import dot
        from pytools.arithmetic_container import join_fields

        dim = self.discr.dimensions

        dtag = self.dirichlet_tag
        ntag = self.neumann_tag

        dirichlet_bc_u = self.dirichlet_bc.boundary_interpolant(self.discr, dtag)
        vpart = self.m_inv * (
                (self.flux_u_dbdry*pair_with_boundary(0, dirichlet_bc_u, dtag))
                )
        diff_v = self.diffusion * vpart

        def neumann_bc_v():
            from pytools.arithmetic_container import work_with_arithmetic_containers
            ac_multiply = work_with_arithmetic_containers(num.multiply)

            return self.neu_diff * ac_multiply(self.neumann_normals,
                    self.neumann_bc.boundary_interpolant(self.discr, ntag))

        w = join_fields(0, diff_v)
        dirichlet_bc_w = join_fields(dirichlet_bc_u, [0]*dim)
        neumann_bc_w = join_fields(0, neumann_bc_v())

        return self.discr.mass_operator * rhs.volume_interpolant(self.discr) - (
                -dot(self.stiff_t, diff_v)
                + self.flux_v * w
                + self.flux_v_dbdry * pair_with_boundary(w, dirichlet_bc_w, dtag)
                + self.flux_v_nbdry * pair_with_boundary(w, neumann_bc_w, ntag)
                )




class StrongHeatOperator(TimeDependentOperator):
    def __init__(self, discr, coeff=hedge.data.ConstantGivenFunction(1), 
            dirichlet_bc=hedge.data.ConstantGivenFunction(), dirichlet_tag="dirichlet",
            neumann_bc=hedge.data.ConstantGivenFunction(), neumann_tag="neumann",
            ldg=True):
        self.discr = discr

        fs = self.get_strong_flux_set(ldg)

        self.flux_u = discr.get_flux_operator(fs.flux_u)
        self.flux_v = discr.get_flux_operator(fs.flux_v)
        self.flux_u_dbdry = discr.get_flux_operator(fs.flux_u_dbdry)
        self.flux_v_dbdry = discr.get_flux_operator(fs.flux_v_dbdry)
        self.flux_u_nbdry = discr.get_flux_operator(fs.flux_u_nbdry)
        self.flux_v_nbdry = discr.get_flux_operator(fs.flux_v_nbdry)

        self.nabla = discr.nabla
        self.stiff = discr.stiffness_operator
        self.mass = discr.mass_operator
        self.m_inv = discr.inverse_mass_operator

        from hedge.mesh import check_bc_coverage
        check_bc_coverage(discr.mesh, [dirichlet_tag, neumann_tag])

        def fast_diagonal_mat(vec):
            return num.diagonal_matrix(vec, flavor=num.SparseExecuteMatrix)

        self.sqrt_coeff = fast_diagonal_mat(
                num.sqrt(coeff.volume_interpolant(discr)))
        self.dir_sqrt_coeff = fast_diagonal_mat(
                num.sqrt(coeff.boundary_interpolant(discr, dirichlet_tag)))
        self.neu_sqrt_coeff = fast_diagonal_mat(
                num.sqrt(coeff.boundary_interpolant(discr, neumann_tag)))

        self.dirichlet_bc = dirichlet_bc
        self.dirichlet_tag = dirichlet_tag
        self.neumann_bc = neumann_bc
        self.neumann_tag = neumann_tag

        self.neumann_normals = discr.boundary_normals(self.neumann_tag)

    # fluxes ------------------------------------------------------------------
    def get_weak_flux_set(self, ldg):
        class FluxSet: pass
        fs = FluxSet()

        from hedge.flux import FluxVectorPlaceholder, FluxScalarPlaceholder, make_normal

        # note here:

        # local DG is unlike the other kids in that the computation of the flux
        # of u depends *only* on u, whereas the computation of the flux of v
        # (yielding the final right hand side) may also depend on u. That's why
        # we use the layout [u,v], where v is simply omitted for the u flux
        # computation.

        dim = self.discr.dimensions
        vec = FluxVectorPlaceholder(1+dim)
        fs.u = u = vec[0]
        fs.v = v = vec[1:]
        normal = fs.normal = make_normal(dim)

        from hedge.tools import dot

        # central
        fs.flux_u = u.avg*normal
        fs.flux_v = dot(v.avg, normal)

        # dbdry is "dirichlet boundary"
        # nbdry is "neumann boundary"
        fs.flux_u_dbdry = fs.flux_u
        fs.flux_u_nbdry = fs.flux_u

        fs.flux_v_dbdry = fs.flux_v
        fs.flux_v_nbdry = fs.flux_v

        if ldg:
            from pytools.arithmetic_container import ArithmeticList 
            ldg_beta = ArithmeticList([1]*dim)

            fs.flux_u = fs.flux_u - (u.int-u.ext)*0.5*ldg_beta
            fs.flux_v = fs.flux_v + dot((v.int-v.ext)*0.5, ldg_beta)

        return fs

    def get_strong_flux_set(self, ldg):
        from hedge.tools import dot

        fs = self.get_weak_flux_set(ldg)

        u = fs.u
        v = fs.v
        normal = fs.normal

        fs.flux_u = u.int*normal - fs.flux_u
        fs.flux_v = dot(v.int, normal) - fs.flux_v
        fs.flux_u_dbdry = u.int*normal - fs.flux_u_dbdry
        fs.flux_v_dbdry = dot(v.int, normal) - fs.flux_v_dbdry
        fs.flux_u_nbdry = u.int*normal - fs.flux_u_nbdry
        fs.flux_v_nbdry = dot(v.int, normal) - fs.flux_v_nbdry

        return fs

    # boundary conditions -----------------------------------------------------
    def dirichlet_bc_u(self, t, sqrt_coeff_u):
        return (
                -self.discr.boundarize_volume_field(sqrt_coeff_u, self.dirichlet_tag)
                +2*self.dir_sqrt_coeff*self.dirichlet_bc.boundary_interpolant(
                    t, self.discr, self.dirichlet_tag)
                )

    def dirichlet_bc_v(self, t, sqrt_coeff_v):
        return self.discr.boundarize_volume_field(sqrt_coeff_v, self.dirichlet_tag)

    def neumann_bc_u(self, t, sqrt_coeff_u):
        return self.discr.boundarize_volume_field(sqrt_coeff_u, self.neumann_tag)

    def neumann_bc_v(self, t, sqrt_coeff_v):
        from pytools.arithmetic_container import work_with_arithmetic_containers

        ac_multiply = work_with_arithmetic_containers(num.multiply)

        return (
                -self.discr.boundarize_volume_field(sqrt_coeff_v, self.neumann_tag)
                +
                2*ac_multiply(self.neumann_normals,
                self.neumann_bc.boundary_interpolant(t, self.discr, self.neumann_tag))
                )

    # right-hand side ---------------------------------------------------------
    def rhs(self, t, u):
        from hedge.discretization import pair_with_boundary
        from math import sqrt
        from hedge.tools import dot
        from pytools.arithmetic_container import join_fields

        dtag = self.dirichlet_tag
        ntag = self.neumann_tag

        sqrt_coeff_u = self.sqrt_coeff * u

        dirichlet_bc_u = self.dirichlet_bc_u(t, sqrt_coeff_u)
        neumann_bc_u = self.neumann_bc_u(t, sqrt_coeff_u)

        v = self.m_inv * (
                self.sqrt_coeff*(self.stiff * u)
                - self.flux_u*sqrt_coeff_u
                - self.flux_u_dbdry*pair_with_boundary(sqrt_coeff_u, dirichlet_bc_u, dtag)
                - self.flux_u_nbdry*pair_with_boundary(sqrt_coeff_u, neumann_bc_u, ntag)
                )
        sqrt_coeff_v = self.sqrt_coeff * v

        dirichlet_bc_v = self.dirichlet_bc_v(t, sqrt_coeff_v)
        neumann_bc_v = self.neumann_bc_v(t, sqrt_coeff_v)

        w = join_fields(sqrt_coeff_u, sqrt_coeff_v)
        dirichlet_bc_w = join_fields(dirichlet_bc_u, dirichlet_bc_v)
        neumann_bc_w = join_fields(neumann_bc_u, neumann_bc_v)

        return self.m_inv * (
                dot(self.stiff, self.sqrt_coeff*v)
                - self.flux_v * w
                - self.flux_v_dbdry * pair_with_boundary(w, dirichlet_bc_w, dtag)
                - self.flux_v_nbdry * pair_with_boundary(w, neumann_bc_w, ntag)
                )




def diagonal_blocks(poisson_op):
    op = poisson_op
    discr = op.discr

    from pytools.arithmetic_container import ArithmeticList
    AL = ArithmeticList

    def multiply_minv_by_diagonal(diag):
        for elgroup in discr.element_groups:
            d_minv = num.diagonal(elgroup.local_discretization.inverse_mass_matrix())
            for el_start, el_end in elgroup.ranges:
                diag[el_start:el_end] = num.multiply(d_minv, diag[el_start:el_end])

        return diag

    def inner_flux_diagonal(int_flux):
        diag = discr.volume_zeros()

        for fg, fmm in discr.face_groups:
            for fp in fg.face_pairs:
                flux_face = fg.flux_faces[fp.flux_face_index]
                local_coeff = flux_face.face_jacobian*int_flux(flux_face, None)
                for i, ili in enumerate(fg.index_lists[fp.face_index_list_number]):
                    diag[ili] += local_coeff*fmm[i,i]

        return multiply_minv_by_diagonal(diag)

    def bdry_flux_diagonal(int_flux, tag):
        diag = discr.volume_zeros()

        bdry = discr._get_boundary(tag)

        if bdry.nodes:
            for fg, ldis in bdry.face_groups_and_ldis:
                fmm = ldis.face_mass_matrix()
                for fp in fg.face_pairs:
                    flux_face = fg.flux_faces[fp.flux_face_index]
                    opp_flux_face = fg.flux_faces[fp.opp_flux_face_index]
                    local_coeff = flux_face.face_jacobian*int_flux(flux_face, opp_flux_face)
                    for i, ili in enumerate(fg.index_lists[fp.face_index_list_number]):
                        diag[ili] += local_coeff*fmm[i,i]

        return multiply_minv_by_diagonal(diag)

    dblocks = {}
    for elgroup in discr.element_groups:
        grad_matrices = AL(
                AL(dot(
                    elgroup.inverse_mass_matrix*(-elgroup.stiffness_t_matrices),
                    (eg.stiffness_coefficients[coord][rst_axis][i_el]
                        for rst_axis in range(discr.dimensions))
                    )
                    for xyz_axis in range(discr.dimensions))
                
                for i_el in range(len(elgroup.members))
                )

        @work_with_arithmetic_containers
        def get_matrix(op):
            return op.matrix

        global_grad_matrix = self.m_inv.matrix() * (- get_matrix(self.stiff_t))



def solve_twogrid_problem(pcon, fine_discr, fine_op, fine_rhs,
        coarse_discr, coarse_op, tol=1e-9):
    # see
    # C. Wagner, Introduction to Algebraic Multigrid.  Heidelberg: 1998.
    # Algorithm 2.3.1

    from hedge.discretization import Projector
    R = Projector(fine_discr, coarse_discr)
    P = Projector(coarse_discr, fine_discr)

    from hedge.visualization import VtkVisualizer
    vis = VtkVisualizer(fine_discr, pcon)
    
    from hedge.tools import PylinearOperator, CGStateContainer

    coarse_cg = CGStateContainer(pcon, coarse_op)
    fine_cg = CGStateContainer(pcon, fine_op)

    delta_0 = fine_cg.reset(fine_rhs)

    max_iterations = 10 * fine_op.size1()
    iteration = 0
    halfstep = 0

    def plot(with_coarse):
        data = [
            ("x", fine_cg.x),
            ("residual", fine_cg.residual),
            ("d", fine_cg.d),
            ]
        if with_coarse:
            data.extend([
                ("coarse_rhs", P(coarse_rhs)),
                ("coarse_correction", P(coarse_cg.x)),
                ])
        else:
            data.extend([
                ("coarse_rhs", fine_discr.volume_zeros()),
                ("coarse_correction", fine_discr.volume_zeros()),
                ])
        visf = vis.make_file("mg-%04d" % halfstep)
        vis.add_data(visf, data, time=halfstep)
        visf.close()

    while iteration < max_iterations:
        # smooth
        for i in range(20):
            delta = fine_cg.one_iteration()

        print "%05i: delta before coarse: %g" % (halfstep, delta)

        plot(with_coarse=False);halfstep += 1

        # solve
        coarse_rhs = R(fine_rhs - fine_op(fine_cg.x))
        coarse_cg.reset(coarse_rhs)
        coarse_cg.run(tol=tol)

        delta = fine_cg.reset(fine_rhs, fine_cg.x+P(coarse_cg.x))
        print "%05i: delta after coarse:  %g" % (halfstep, delta)

        if abs(delta) < tol*tol * abs(delta_0):
            return fine_cg.x

        plot(with_coarse=True);halfstep += 1

        iteration += 1

        print "-----------------------------------------"
