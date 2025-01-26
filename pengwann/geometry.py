"""
Parse periodic structures, assign Wannier centres and identify interatomic interactions.

This module contains the functions necessary to parse the geometry of the target system
and from this identify relevant interatomic interactions from which to compute bonding
descriptors.
"""

# Copyright (C) 2024-2025 Patrick J. Taylor

# This file is part of pengWann.
#
# pengWann is free software: you can redistribute it and/or modify it under the terms
# of the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version.
#
# pengWann is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
# PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with pengWann.
# If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import numpy as np
from collections.abc import Sequence
from numpy.typing import ArrayLike, NDArray
from pengwann.utils import get_atom_indices, integrate_descriptor
from pymatgen.core import Lattice, Molecule, Structure
from typing import NamedTuple


class AtomicInteraction(NamedTuple):
    """
    Stores data regarding the interaction between two atoms.

    Within :code:`pengwann`, the interaction between two atoms is comprised of the
    interactions between their associated Wannier functions, hence each
    AtomicInteraction object is associated with a set of WannierInteraction objects.

    Attributes
    ----------
    pair_id : tuple[str, str]
        A pair of strings identifying the interacting atoms such as ("Ga1", "As2").
    wannier_interactions : iterable[WannierInteraction]
        The set of WannierInteraction objects associated with the interactions between
        the interacting atoms' Wannier functions.
    dos_matrix : ndarray[float] | None, optional
        The DOS matrix associated with the interaction. Defaults to None.
    wohp : ndarray[float] | None, optional
        The WOHP associated with the interaction. Defaults to None.
    wobi : ndarray[float] | None, optional
        The WOBI associated with the interaction. Defaults to None.
    iwohp : float | ndarray[float] | None, optional
        The IWOHP (integrated WOHP) associated with the interaction. Defaults to None.
    iwobi : float | ndarray[float] | None, optional
        The IWOBI (integrated WOBI) associated with the interaction. Defaults to None.
    population : float | ndarray[float] | None, optional
        The population (integrated DOS matrix) associated with the interaction. Defaults
        to None.
    charge : float | ndarray[float] | None, optional
        The charge associated with the interaction (generally this only makes sense for
        on-site/diagonal interactions where atom_i == atom_j).

    Notes
    -----
    It is expected that this class will normally be initialised with solely the data
    required to specify the interacting atoms: the labels `pair_id` and the set of
    WannierInteraction objects `wannier_interactions`. The remaining fields will usually
    only be set by methods of the :py:class:`~pengwann.descriptors.DescriptorCalculator`
    class.
    """

    pair_id: tuple[str, str]
    wannier_interactions: Sequence[WannierInteraction]

    dos_matrix: NDArray[np.float64] | None = None
    wohp: NDArray[np.float64] | None = None
    wobi: NDArray[np.float64] | None = None
    iwohp: np.float64 | NDArray[np.float64] | None = None
    iwobi: np.float64 | NDArray[np.float64] | None = None
    population: np.float64 | NDArray[np.float64] | None = None
    charge: np.float64 | NDArray[np.float64] | None = None

    def with_summed_descriptors(self) -> AtomicInteraction:
        new_values = {}

        descriptor_keys = ("dos_matrix", "wohp", "wobi")
        for descriptor_key in descriptor_keys:
            calculated = True

            for w_interaction in self.wannier_interactions:
                if w_interaction.dos_matrix is None:
                    raise TypeError

                if descriptor_key == "wohp":
                    if w_interaction.h_ij is None:
                        calculated = False
                        break

                if descriptor_key == "wobi":
                    if w_interaction.p_ij is None:
                        calculated = False
                        break

            if calculated:
                new_values[descriptor_key] = sum(
                    [
                        getattr(w_interaction, descriptor_key)
                        for w_interaction in self.wannier_interactions
                    ]
                )

        return self._replace(**new_values)

    def with_integrals(
        self,
        energies: NDArray[np.float64],
        mu: float,
        resolve_orbitals: bool = False,
        valence: int | None = None,
    ) -> AtomicInteraction:
        new_values = {}

        if self.dos_matrix is not None:
            new_values["population"] = integrate_descriptor(
                energies, self.dos_matrix, mu
            )

            if valence is not None:
                new_values["charge"] = valence - new_values["population"]

        if self.wohp is not None:
            new_values["iwohp"] = integrate_descriptor(energies, self.wohp, mu)

        if self.wobi is not None:
            new_values["iwobi"] = integrate_descriptor(energies, self.wobi, mu)

        if resolve_orbitals:
            new_values["wannier_interactions"] = []
            for w_interaction in self.wannier_interactions:
                updated_wannier_interaction = w_interaction.integrate(energies, mu)

                new_values["wannier_interactions"].append(updated_wannier_interaction)

        return self._replace(**new_values)


class WannierInteraction(NamedTuple):
    """
    Stores data regarding the interaction between two Wannier functions.

    Attributes
    ----------
    i : int
        The index identifying Wannier function i.
    j : int
        The index identifying Wannier function j.
    bl_1 : ndarray[np.int_]
        The Bravais lattice vector specifying the translation of Wannier function i
        relative to its home cell.
    bl_2 : ndarray[np.int_]
        The Bravais lattice vector specifying the translation of Wannier function j
        relative to its home cell.
    dos_matrix : ndarray[float] | None, optional
        The DOS matrix associated with the interaction. Defaults to None.
    wohp
    wobi
    h_ij : float | None, optional
        The element of the Wannier Hamiltonian associated with the interaction. Defaults
        to None.
    p_ij : float | None, optional
        The element of the Wannier density matrix associated with the interaction.
        Defaults to None.
    iwohp : float | ndarray[float] | None, optional
        The IWOHP (integrated WOHP) associated with the interaction. Defaults to None.
    iwobi : float | ndarray[float] | None, optional
        The IWOBI (integrated WOBI) associated with the interaction. Defaults to None.
    population : float | ndarray[float] | None, optional
        The population (integrated DOS matrix) associated with the interaction. Defaults
        to None.

    Notes
    -----
    It is expected that this class will normally be initialised with solely the data
    required to specify the interacting Wannier functions: the indices `i` and `j`
    alongside the Bravais lattice vectors `bl_1` and `bl_2`. The remaining fields will
    usually only be set by methods of the
    :py:class:`~pengwann.descriptors.DescriptorCalculator` class.
    """

    i: int
    j: int
    bl_1: NDArray[np.int_]
    bl_2: NDArray[np.int_]

    dos_matrix: NDArray[np.float64] | None = None
    h_ij: np.float64 | None = None
    p_ij: np.float64 | None = None
    iwohp: np.float64 | NDArray[np.float64] | None = None
    iwobi: np.float64 | NDArray[np.float64] | None = None
    population: np.float64 | NDArray[np.float64] | None = None

    @property
    def wohp(self) -> NDArray[np.float64] | None:
        """
        The WOHP associated with the interaction.

        Returns
        -------
        wohp : ndarray[float] | None
            The WOHP or None (in the case that the DOS matrix or the relevant element
            of the Wannier Hamiltonian are not available).

        Notes
        -----
        The WOHP will be recalculated from the relevant element of the Wannier
        Hamiltonian and the DOS matrix every time this property is accessed (it is not
        cached). This is intended to reduce memory usage and has minimal impact on
        computational cost, owing to the fact that computing the DOS matrix itself is
        by far the most demanding step and this is only done once.
        """
        if self.h_ij is None or self.dos_matrix is None:
            return None

        return -self.h_ij * self.dos_matrix

    @property
    def wobi(self) -> NDArray[np.float64] | None:
        """
        The WOBI associated with the interaction.

        Returns
        -------
        wohp : ndarray[float] | None
            The WOBI or None (in the case that the DOS matrix or the relevant element
            of the Wannier density matrix are not available).

        Notes
        -----
        The WOBI will be recalculated from the relevant element of the Wannier
        density matrix and the DOS matrix every time this property is accessed (it is
        not cached). This is intended to reduce memory usage and has minimal impact on
        computational cost, owing to the fact that computing the DOS matrix itself is
        by far the most demanding step and this is only done once.
        """
        if self.p_ij is None or self.dos_matrix is None:
            return None

        return self.p_ij * self.dos_matrix

    def integrate(self, energies: NDArray[np.float64], mu: float) -> WannierInteraction:
        if self.dos_matrix is None:
            raise TypeError

        new_values = {}

        new_values["population"] = integrate_descriptor(energies, self.dos_matrix, mu)

        wohp = self.wohp
        if wohp is not None:
            new_values["iwohp"] = integrate_descriptor(energies, wohp, mu)

        wobi = self.wobi
        if wobi is not None:
            new_values["iwobi"] = integrate_descriptor(energies, wobi, mu)

        return self._replace(**new_values)


def build_geometry(path: str, cell: ArrayLike) -> Structure:
    """
    Return a Pymatgen Structure with a :code:`"wannier_centres"` site property.

    The "wannier_centres" site property associates each atom in the structure with a
    sequence of indices. These indices indicate the Wannier centres that have been
    assigned to each atom.

    Parameters
    ----------
    path : str
        Filepath to the xyz file output by Wannier90.
    cell : array_like
        The cell vectors associated with the structure.

    Returns
    -------
    geometry : Structure
        The Pymatgen Structure with a :code:`"wannier_centres"` site property.

    Notes
    -----
    The `geometry` returned by this function can be passed as the `geometry` argument
    to several methods of the :py:class:`~pengwann.descriptors.DescriptorCalculator`
    class.
    """
    lattice = Lattice(cell)

    xyz = Molecule.from_file(path)

    assert xyz is not None
    species, coords = [], []
    for site in xyz:
        symbol = site.species_string.capitalize()
        species.append(symbol)
        coords.append(site.coords)

    geometry = Structure(lattice, species, coords, coords_are_cartesian=True)

    assign_wannier_centres(geometry)

    return geometry


def assign_wannier_centres(geometry: Structure) -> None:
    """
    Assign Wannier centres to atoms based on a closest distance criterion.

    A :code:`"wannier_centres"` site property will be added to the input `geometry`,
    associating each atom in the structure with a sequence of indices. These indices
    refer to the order of atoms in `geometry` and associate each atom with the Wannier
    centres to which it is closer than any other atom.

    Parameters
    ----------
    geometry : Structure
        A Pymatgen Structure object containing the structure itself as well as the
        positions of the Wannier centres (as "X" atoms).

    Returns
    -------
    None
    """
    wannier_indices, atom_indices = [], []
    for idx in range(len(geometry)):
        symbol = geometry[idx].species_string

        if symbol == "X0+":
            wannier_indices.append(idx)

        else:
            atom_indices.append(idx)

    if not wannier_indices:
        raise ValueError(
            'No Wannier centres ("X" atoms) found in the input Structure object.'
        )

    distance_matrix = geometry.distance_matrix

    wannier_centres_list: list[list[int]] = [[] for idx in range(len(geometry))]
    for i in wannier_indices:
        min_distance, min_idx = np.inf, 2 * len(geometry)

        for j in atom_indices:
            distance = distance_matrix[i, j]

            if distance < min_distance:
                min_distance = distance
                min_idx = j

        wannier_centres_list[i].append(min_idx)
        wannier_centres_list[min_idx].append(i)

    wannier_centres = tuple([tuple(indices) for indices in wannier_centres_list])
    geometry.add_site_property("wannier_centres", wannier_centres)


def identify_onsite_interactions(
    geometry: Structure, symbols: tuple[str, ...]
) -> tuple[AtomicInteraction, ...]:
    """
    Identify all onsite interactions for a set of atomic species.

    Parameters
    ----------
    geometry : Structure
            A Pymatgen Structure object with a :code:`"wannier_centres"` site property
            that associates each atom with the indices of its Wannier centres.
    symbols : tuple[str, ...]
            The atomic species to return interactions for. These should match one or
            more of the species present in `geometry`.

    Returns
    -------
    interactions : tuple[AtomicInteraction, ...]
            The on-site/diagonal AtomicInteraction objects associated with each symbol
            in `symbols`.

    Notes
    -----
    In the context of pengwann, an on-site/diagonal interaction is simply a 2-body
    interaction between atoms or individual Wannier functions in which
    atom_i == atom_j or wannier_function_i == wannier_function_j.
    """
    bl_0 = np.array([0, 0, 0])
    wannier_centres = geometry.site_properties["wannier_centres"]
    num_wann = len([site for site in geometry if site.species_string == "X0+"])

    interactions = []
    for idx in range(len(geometry)):
        symbol = geometry[idx].species_string
        if symbol in symbols:
            label = symbol + str(idx - num_wann + 1)
            pair_id = (label, label)

            wannier_interactions = []
            for i in wannier_centres[idx]:
                wannier_interaction = WannierInteraction(i, i, bl_0, bl_0)

                wannier_interactions.append(wannier_interaction)

            interaction = AtomicInteraction(pair_id, tuple(wannier_interactions))

            interactions.append(interaction)

    if not interactions:
        raise ValueError(f"No atoms matching symbols in {symbols} found.")

    return tuple(interactions)


def identify_interatomic_interactions(
    geometry: Structure, radial_cutoffs: dict[tuple[str, str], float]
) -> tuple[AtomicInteraction, ...]:
    """
    Identify interatomic interactions according to a set of radial distance cutoffs.

    Parameters
    ----------
    geometry : Structure
        A Pymatgen Structure object with a :code:`"wannier_centres"` site property that
        associates each atom with the indices of its Wannier centres.
    radial_cutoffs : dict[tuple[str, str], float]
        A dictionary defining radial cutoffs for pairs of atomic species e.g
        :code:`{("C", "C"): 1.6, ("C", "O"): 1.5}`.

    Returns
    -------
    interactions : tuple[AtomicInteraction, ...]
        The interactions identified according to the `radial_cutoffs`.

    See Also
    --------
    build_geometry
    pengwann.descriptors.DescriptorCalculator.assign_descriptors :
        Compute bonding descriptors for a set of interatomic interactions.
    """
    if "wannier_centres" not in geometry.site_properties.keys():
        raise ValueError('Input geometry is missing a "wannier_centres" site property.')

    num_wann = len([site for site in geometry if site.species_string == "X0+"])

    if num_wann == 0:
        raise ValueError(
            'Input geometry contains no Wannier centres (i.e. no "X" atoms).'
        )

    symbols_list: list[str] = []
    for pair in radial_cutoffs.keys():
        for symbol in pair:
            if symbol not in symbols_list:
                symbols_list.append(symbol)

    symbols = tuple(symbols_list)

    atom_indices = get_atom_indices(geometry, symbols)

    wannier_centres = geometry.site_properties["wannier_centres"]
    interactions = []
    for pair, cutoff in radial_cutoffs.items():
        symbol_i, symbol_j = pair

        possible_interactions = []
        if symbol_i != symbol_j:
            for i in atom_indices[symbol_i]:
                for j in atom_indices[symbol_j]:
                    possible_interactions.append((i, j))

        # Exclude self-interactions
        else:
            for idx, i in enumerate(atom_indices[symbol_i]):
                for j in atom_indices[symbol_j][idx + 1 :]:
                    possible_interactions.append((i, j))

        for i, j in possible_interactions:
            distance = geometry.get_distance(i, j)

            if distance < cutoff:
                pair_id = (
                    symbol_i + str(i - num_wann + 1),
                    symbol_j + str(j - num_wann + 1),
                )
                wannier_interactions_list = []
                for m in wannier_centres[i]:
                    for n in wannier_centres[j]:
                        _, bl_1 = geometry[i].distance_and_image(geometry[m])
                        _, bl_2 = geometry[j].distance_and_image(geometry[n])

                        wannier_interaction = WannierInteraction(m, n, bl_1, bl_2)
                        wannier_interactions_list.append(wannier_interaction)

                wannier_interactions = tuple(wannier_interactions_list)
                interaction = AtomicInteraction(pair_id, wannier_interactions)
                interactions.append(interaction)

    return tuple(interactions)
