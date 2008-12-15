# Hedge - the Hybrid'n'Easy DG Environment
# Copyright (C) 2008 Andreas Kloeckner
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




from __future__ import division
import numpy
import numpy.linalg as la




class Vortex:
    def __init__(self, beta, gamma, center):
        self.beta = beta
        self.gamma = gamma
        self.center = center

    def __call__(self, t, x_vec):
        x = x_vec[0]
        y = x_vec[1]

        from math import pi
        r = numpy.sqrt((x-t-self.center[0])**2+(y-self.center[1])**2)
        expterm = self.beta*numpy.exp(1-r**2)
        u = 1-expterm*(y-self.center[1])/pi
        v = expterm*(x-self.center[0])/pi
        rho = (1-(self.gamma-1)/(16*self.gamma*pi**2)*expterm**2)**(1/(self.gamma-1))
        p = rho**self.gamma

        e = p/(self.gamma-1) + rho/2*(u**2+v**2)

        from hedge.tools import join_fields
        return join_fields(rho, e, rho*u, rho*v)

    def volume_interpolant(self, t, discr):
        return self(t, discr.nodes.T)

    def boundary_interpolant(self, t, discr, tag):
        return self(t, discr.get_boundary(tag).nodes.T)




def main():
    from hedge.backends import guess_run_context
    rcon = guess_run_context(disable=set(["cuda"]))

    gamma = 1.4

    from hedge.tools import EOCRecorder, to_obj_array
    eoc_rec = EOCRecorder()
    
    if rcon.is_head_rank:
        from hedge.mesh import make_rect_mesh
        mesh = make_rect_mesh((0,-5), (10,5), max_area=0.15)
        mesh_data = rcon.distribute_mesh(mesh)
    else:
        mesh_data = rcon.receive_mesh()

    for order in [3]:
    #for order in [1,2,3,4,5,6]:
        discr = rcon.make_discretization(mesh_data, order=order)

        from hedge.visualization import VtkVisualizer
        vis = VtkVisualizer(discr, rcon, "vortex-%d" % order)

        vortex = Vortex(beta=5, gamma=5, center=[5,0])
        fields = vortex.volume_interpolant(0, discr)

        from hedge.pde import EulerOperator
        op = EulerOperator(dimensions=2, gamma=1.4, bc=vortex)
        #for i, oi in enumerate(op.op_template()):
            #print i, oi

        euler_rhs = op.bind(discr)

        max_eigval = [0]
        def rhs(t, q):
            ode_rhs, speed = euler_rhs(t, q)
            max_eigval[0] = speed
            return ode_rhs
        rhs(0, fields)

        dt = discr.dt_factor(max_eigval[0])
        final_time = 10
        nsteps = int(final_time/dt)+1
        dt = final_time/nsteps

        if rcon.is_head_rank:
            print "---------------------------------------------"
            print "order %d" % order
            print "---------------------------------------------"
            print "dt", dt
            print "nsteps", nsteps
            print "#elements=", len(mesh.elements)

        from hedge.timestep import RK4TimeStepper
        stepper = RK4TimeStepper()

        # diagnostics setup ---------------------------------------------------
        from pytools.log import LogManager, add_general_quantities, \
                add_simulation_quantities, add_run_info

        logmgr = LogManager("euler-%d.dat" % order, "w", rcon.communicator)
        add_run_info(logmgr)
        add_general_quantities(logmgr)
        add_simulation_quantities(logmgr, dt)
        discr.add_instrumentation(logmgr)
        stepper.add_instrumentation(logmgr)

        logmgr.add_watches(["step.max", "t_sim.max", "t_step.max"])

        # timestep loop -------------------------------------------------------
        t = 0


        for step in range(nsteps):
            logmgr.tick()

            if True:
                visf = vis.make_file("vortex-%d-%04d" % (order, step))
                vis.add_data(visf,
                        [
                            ("rho", op.rho(fields)),
                            ("e", op.e(fields)),
                            ("rho_u", op.rho_u(fields)),
                            ("u", op.u(fields)),
                            ],
                        time=t, step=step
                        )
                visf.close()

            fields = stepper(fields, t, dt, rhs)
            t += dt

            dt = discr.dt_factor(max_eigval[0])

        logmgr.tick()
        logmgr.save()

        if False:
            numpy.seterr('raise')
            mode.set_time(t)
            true_fields = to_obj_array(mode(discr).real)

            eoc_rec.add_data_point(order, discr.norm(fields-true_fields))

            print
            print eoc_rec.pretty_print("P.Deg.", "L2 Error")

if __name__ == "__main__":
    main()