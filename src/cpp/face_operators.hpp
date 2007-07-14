// Hedge - the Hybrid'n'Easy DG Environment
// Copyright (C) 2007 Andreas Kloeckner
// 
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
// 
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
// 
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.




#ifndef _ASFAHDALSU_HEDGE_FACE_OPERATORS_HPP_INCLUDED
#define _ASFAHDALSU_HEDGE_FACE_OPERATORS_HPP_INCLUDED




#include <boost/foreach.hpp>
#include <vector>
#include <utility>
#include "base.hpp"
#include "flux.hpp"




namespace hedge {
  struct face_group 
  {
    typedef std::vector<unsigned> index_list;
    struct face_info
    {
      index_list face_indices;
      index_list opposite_indices;
      flux::face flux_face;
      flux::face *opp_flux_face;
    };

    std::vector<face_info> m_face_infos;

    unsigned size()
    {
      return m_face_infos.size();
    }

    void clear()
    {
      m_face_infos.clear();
    }

    void add_face(const index_list &my_ind, const index_list &opp_ind, 
        const flux::face &face)
    {
      face_info fi;
      fi.face_indices = my_ind;
      fi.opposite_indices = opp_ind;
      fi.flux_face = face;
      fi.opp_flux_face = 0;
      m_face_infos.push_back(fi);
    }

    typedef std::pair<unsigned, unsigned> connection;
    typedef std::vector<connection> connection_list;

    void connect_faces(const connection_list &cnx_list)
    {
      BOOST_FOREACH(const connection &cnx, cnx_list)
        m_face_infos[cnx.first].opp_flux_face = &m_face_infos[cnx.second].flux_face;
    }
  };




  template <class Mat, class Flux, class OT>
  inline
  void perform_both_fluxes_operator(const face_group &fg, 
      const Mat &fmm, Flux flux, OT target)
  {
    unsigned face_length = fmm.size1();

    assert(fmm.size1() == fmm.size2());

    BOOST_FOREACH(const face_group::face_info &fi, fg.m_face_infos)
    {
      double local_coeff = flux.local_coeff(fi.flux_face);
      double neighbor_coeff = flux.neighbor_coeff(
          fi.flux_face, fi.opp_flux_face);

      assert(fmm.size1() == fi.face_indices.size());
      assert(fmm.size1() == fi.opp_indices.size());

      for (unsigned i = 0; i < face_length; i++)
        for (unsigned j = 0; j < face_length; j++)
        {
          target.add_coefficient(fi.face_indices[i], fi.face_indices[j],
              fi.flux_face.face_jacobian*local_coeff*fmm(i, j));
          target.add_coefficient(fi.face_indices[i], fi.opposite_indices[j],
              fi.flux_face.face_jacobian*neighbor_coeff*fmm(i, j));
        }
    }
  }




  template <class Mat, class Flux, class OT>
  inline
  void perform_local_flux_operator(const face_group &fg, 
      const Mat &fmm, Flux flux, OT target)
  {
    unsigned face_length = fmm.size1();

    assert(fmm.size1() == fmm.size2());

    BOOST_FOREACH(const face_group::face_info &fi, fg.m_face_infos)
    {
      double local_coeff = flux.local_coeff(fi.flux_face);

      assert(fmm.size1() == fi.face_indices.size());

      for (unsigned i = 0; i < face_length; i++)
        for (unsigned j = 0; j < face_length; j++)
        {
          target.add_coefficient(fi.face_indices[i], fi.face_indices[j],
              fi.flux_face.face_jacobian*local_coeff*fmm(i, j));
        }
    }
  }




  template <class Mat, class Flux, class OT>
  inline
  void perform_neighbor_flux_operator(const face_group &fg, 
      const Mat &fmm, Flux flux, OT target)
  {
    unsigned face_length = fmm.size1();

    assert(fmm.size1() == fmm.size2());

    BOOST_FOREACH(const face_group::face_info &fi, fg.m_face_infos)
    {
      double neighbor_coeff = flux.neighbor_coeff(
          fi.flux_face, fi.opp_flux_face);

      assert(fmm.size1() == fi.opp_indices.size());

      for (unsigned i = 0; i < face_length; i++)
        for (unsigned j = 0; j < face_length; j++)
        {
          target.add_coefficient(fi.face_indices[i], fi.opposite_indices[j],
              fi.flux_face.face_jacobian*neighbor_coeff*fmm(i, j));
        }
    }
  }
}




#endif