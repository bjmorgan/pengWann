"""
Chemical bonding descriptors from Wannier functions.

This module contains a single class
(:py:class:`~pengwann.descriptors.DescriptorCalculator`) which contains the core
functionality of :code:`pengwann`: computing various descriptors of chemical bonding
from Wannier functions as output by Wannier90.
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

import warnings
import numpy as np
from collections.abc import Iterable
from multiprocessing import Pool
from multiprocessing.shared_memory import SharedMemory
from numpy.typing import NDArray
from pengwann.geometry import AtomicInteraction, WannierInteraction
from pengwann.utils import allocate_shared_memory, parse_id
from pymatgen.core import Structure
from tqdm.auto import tqdm
from typing import Any, Optional


class DescriptorCalculator:
    r"""
    Compute descriptors of chemical bonding and local electronic structure.

    This class can be used to calculate:

    - Wannier orbital Hamilton populations (WOHPs) + integrals (IWOHPs)
    - Wannier orbital bond indices (WOBIs) + integrals (IWOBIs)
    - The projected density of states (pDOS)
    - Wannier-function-resolved populations
    - Atomic charges
    - The density of energy (DOE)
    - Bond-weighted distribution functions (BWDFs)

    Parameters
    ----------
    dos_array : ndarray[float]
        The density of states discretised across energies, k-points and bands.
    num_wann : int
        The total number of Wannier functions.
    nspin : int
        The number of electrons per fully-occupied band. This should be set to 2 for
        non-spin-polarised calculations and set to 1 for spin-polarised calculations.
    kpoints : ndarray[float]
        The full k-point mesh used in the prior Wannier90 calculation.
    u : ndarray[complex]
        The U matrices that define the Wannier functions in terms of the canonical
        Bloch states.
    h : dict[tuple[int, ...], ndarray[complex]] | None, optional
        The Hamiltonian in the Wannier basis. Required for the computation of WOHPs.
        Defaults to None.
    occupation_matrix : ndarray[float] | None, optional
        The Kohn-Sham occupation matrix. Required for the computation of WOBIs.
        Defaults to None.
    energies : ndarray[float] | None, optional
        The energies at which the `dos_array` has been evaluated. Defaults to None.

    Returns
    -------
    None

    Notes
    -----
    Upon initialisation, the spilling factor will be calculated. The spilling factor is
    defined as :footcite:p:`spilling, WOHP`

    .. math::

        S = \frac{1}{N_{k}}\frac{1}{N_{w}}\sum_{nk} 1 - \sum_{\alpha}
        |\braket{\psi_{nk}|w_{\alpha}}|^{2},

    where :math:`N_{k}` is the total number of k-points, :math:`N_{w}` is the total
    number of Wannier functions, :math:`n` labels bands, :math:`k` labels k-points and
    :math:`\alpha` labels Wannier functions :math:`\ket{w_{\alpha}}`.

    For Wannier functions derived from energetically isolated bands, the spilling
    factor should be (within machine precision) strictly 0. For disentangled bands,
    the spilling factor should still ideally be very close to 0. If the calculated
    spilling factor is > 0, a warning will be printed to the console and all derived
    results should be treated with caution.

    This class should not normally be initialised using the base constructor. See
    instead the :py:meth:`~pengwann.descriptors.DescriptorCalculator.from_eigenvalues`
    classmethod.

    References
    ----------
    .. footbibliography::
    """

    _bl_0 = np.array((0, 0, 0))

    def __init__(
        self,
        dos_array: NDArray[np.float64],
        num_wann: int,
        nspin: int,
        kpoints: NDArray[np.float64],
        u: NDArray[np.complex128],
        h: Optional[dict[tuple[int, ...], NDArray[np.complex128]]] = None,
        occupation_matrix: Optional[NDArray[np.float64]] = None,
        energies: Optional[NDArray[np.float64]] = None,
    ):
        self._dos_array = dos_array
        self._num_wann = num_wann
        self._nspin = nspin
        self._kpoints = kpoints
        self._u = u
        self._h = h
        self._occupation_matrix = occupation_matrix
        self._energies = energies

        u_star = np.conj(self._u)
        overlaps = (u_star * self._u).real

        num_dp = 8
        spilling_factor = abs(
            round(1 - np.sum(overlaps) / len(self._kpoints) / self._num_wann, num_dp)
        )

        if spilling_factor > 0:
            warnings.warn(
                f"""
            The spilling factor = {spilling_factor}.

            It is advisable to verify that the spilling factor is sufficiently low. For
            Wannier functions derived from energetically isolated bands, it should be
            (within machine precision) strictly 0. For Wannier functions derived using
            disentanglement, the spilling factor should still be very close to 0.

            If the spilling factor is significantly > 0, this implies that there are
            parts of the Bloch subspace that the Wannier basis does not span and thus
            any results derived from the Wannier basis should be analysed with caution.
            """
            )

    @classmethod
    def from_eigenvalues(
        cls,
        eigenvalues: NDArray[np.float64],
        num_wann: int,
        nspin: int,
        energy_range: tuple[float, float],
        resolution: float,
        sigma: float,
        kpoints: NDArray[np.float64],
        u: NDArray[np.complex128],
        h: Optional[dict[tuple[int, ...], NDArray[np.complex128]]] = None,
        occupation_matrix: Optional[NDArray[np.float64]] = None,
    ) -> DescriptorCalculator:
        """
        Initialise a DescriptorCalculator object from a set of Kohn-Sham eigenvalues.

        Parameters
        ----------
        eigenvalues : ndarray[float]
            The Kohn-Sham eigenvalues.
        num_wann : int
            The total number of Wannier functions.
        nspin : int
            The number of electrons per fully-occupied band. This should be set to 2
            for non-spin-polarised calculations and set to 1 for spin-polarised
            calculations.
        energy_range : tuple[float, float]
            The energy range over which the density of states is to be evaluated.
        resolution : float
            The desired energy resolution of the density of states.
        sigma : float
            The width of the Gaussian kernel used to smear the density of states (in eV).
        kpoints : ndarray[float]
            The full k-point mesh used in the prior Wannier90 calculation.
        u : ndarray[complex]
            The U matrices that define the Wannier functions in terms of the canonical
            Bloch states.
        h : dict[tuple[int, ...], ndarray[complex]] | None, optional
            The Hamiltonian in the Wannier basis. Required for the computation of WOHPs.
            Defaults to None.
        occupation_matrix : ndarray[float] | None, optional
            The Kohn-Sham occupation matrix. Required for the computation of WOBIs.
            Defaults to None.

        Returns
        -------
        descriptor_calculator : DescriptorCalculator
            The initialised DescriptorCalculator object.

        See Also
        --------
        pengwann.io.read : Parse Wannier90 output files.
        pengwann.utils.get_occupation_matrix
        """
        emin, emax = energy_range
        energies = np.arange(emin, emax + resolution, resolution, dtype=np.float64)

        x_mu = energies[:, np.newaxis, np.newaxis] - eigenvalues
        dos_array = (
            1
            / np.sqrt(np.pi * sigma)
            * np.exp(-(x_mu**2) / sigma)
            / eigenvalues.shape[1]
        )
        dos_array = np.swapaxes(dos_array, 1, 2)

        return cls(
            dos_array, num_wann, nspin, kpoints, u, h, occupation_matrix, energies
        )

    @property
    def energies(self) -> Optional[NDArray[np.float64]]:
        """
        The discrete energies over which the DOS (and derived descriptors) has been evaluated.

        Returns
        -------
        energies : ndarray[float] | None
            The energies over which the DOS (and all derived quantities such as WOHPs
            or WOBIs) has been evaluated. If these energies were not provided when the
            constructor was called, this property will simply return None.
        """
        return self._energies

    def get_coefficient_matrix(
        self, i: int, bl_vector: NDArray[np.int_]
    ) -> NDArray[np.complex128]:
        r"""
        Calculate the coefficient matrix for a given Wannier function.

        Parameters
        ----------
        i : int
            The index identifying the target Wannier function.
        bl_vector : ndarray of np.int_
            The Bravais lattice vector specifying the translation of Wannier function
            i from its home cell.

        Returns
        -------
        c : ndarray[complex]
            The coefficient matrix.

        Notes
        -----
        The coefficient matrix :math:`C^{\alpha}` for a given Wannier function
        :math:`\ket{w_{iR}} = \ket{w_{\alpha}}` has dimensions of num_kpoints x
        num_bands. Each element is constructed as :footcite:p:`WOHP`

        .. math::

            C^{\alpha}_{nk} = \exp[ik \cdot R]\left(U^{k}_{ni}\right)^{*},

        where :math:`\alpha` combines the values of the `i` and `bl_vector` arguments
        (it is a combined index that identifies a particular Wannier function), :math:`n`
        is a band index, :math:`k` is a k-point and :math:`U` refers to the unitary
        matrices that mix Bloch vectors to produce Wannier functions. Note that within
        the exponential term, :math:`i = \sqrt{-1}`, whereas it acts as a Wannier
        function index with respect to :math:`U`.

        References
        ----------
        .. footbibliography::
        """
        c = (np.exp(1j * 2 * np.pi * self._kpoints @ bl_vector))[
            :, np.newaxis
        ] * np.conj(self._u[:, :, i])

        return c

    def get_dos_matrix(
        self,
        c_star: NDArray[np.complex128],
        c: NDArray[np.complex128],
        resolve_k: bool = False,
    ) -> NDArray[np.float64]:
        r"""
        Calculate the DOS matrix for a pair of Wannier functions.

        Parameters
        ----------
        c_star : ndarray[complex]
            The coefficient matrix for Wannier function i with Bravais lattice vector
            R_1.
        c : ndarray[complex]
            The coefficient matrix for Wannier function j with Bravais lattice vector
            R_2.
        resolve_k : bool, optional
            Whether or not to resolve the DOS matrix with respect to k-points. Defaults
            to False.

        Returns
        -------
        dos_matrix : ndarray[float]
            The DOS matrix.

        See Also
        --------
        get_coefficient_matrix

        Notes
        -----
        For `resolve_k` = True, the DOS matrix :math:`D_{\alpha\beta}` for a given pair
        of Wannier functions :math:`\ket{w_{\alpha}}` and :math:`\ket{w_{\beta}}` has
        dimensions of num_energy x num_kpoints, where num_energy refers to the number
        of discrete energies over which the density of states has been evaluated. For
        `resolve_k` = False, it is no longer a DOS matrix but rather a DOS vector with
        num_energy elements.

        For the k-resolved case, each element of the DOS matrix is constructed as
        :footcite:p:`original_COHP`

        .. math::

            D_{\alpha\beta}(E, k) = \sum_{n} \mathrm{Re}\left[\left(C^{\alpha}_{nk}
            \right)^{*}C^{\beta}_{nk}\right] \cdot \delta(\epsilon_{nk} - E),

        where :math:`\left(C^{\alpha}\right)^{*}` and :math:`C^{\beta}` reflect the
        values of the `c_star` and `c` arguments and :math:`\delta(\epsilon_{nk} - E)`
        is the density of states evaluated for a particular band and k-point. Summing
        over :math:`k` (`resolve_k` = False) yields

        .. math::

            D_{\alpha\beta}(E) = \sum_{k} D_{\alpha\beta}(E, k),

        which is the aforementioned DOS vector.

        References
        ----------
        .. footbibliography::
        """
        dos_matrix_nk = (
            self._nspin * (c_star * c)[np.newaxis, :, :].real * self._dos_array
        )

        if resolve_k:
            dos_matrix = np.sum(dos_matrix_nk, axis=2)

        else:
            dos_matrix = np.sum(dos_matrix_nk, axis=(1, 2))

        return dos_matrix

    def get_density_matrix_element(
        self, c_star: NDArray[np.complex128], c: NDArray[np.complex128]
    ) -> np.complex128:
        r"""
        Calculate an element of the Wannier density matrix.

        Parameters
        ----------
        c_star : ndarray[complex]
            The coefficient matrix for Wannier function i with Bravais lattice vector
            R_1.
        c : ndarray[complex]
            The coefficient matrix for Wannier function j with Bravais lattice vector
            R_2.

        Returns
        -------
        element : complex
            An element of the Wannier density matrix.

        See Also
        --------
        get_coefficient_matrix

        Notes
        -----
        A given element of the Wannier density matrix is constructed as

        .. math::

            P_{\alpha\beta} = \sum_{nk} w_{k}f_{nk}\left(C^{\alpha}_{nk}\right)^{*}
            C^{\beta}_{nk},

        where :math:`\left(C^{\alpha}\right)^{*}` and :math:`C^{\beta}` refer to the
        `c_star` and `c` arguments, :math:`f` is the occupation matrix and
        :math:`\{w_{k}\}` are k-point weights.
        """
        if self._occupation_matrix is None:
            raise TypeError(
                "The occupation matrix is required to calculate elements of the Wannier density matrix."
            )

        p_nk = self._occupation_matrix * c_star * c

        element = np.sum(p_nk, axis=(0, 1)) / len(self._kpoints)

        return element

    def get_pdos(
        self,
        geometry: Structure,
        symbols: tuple[str, ...],
        resolve_k: bool = False,
        n_proc: int = 4,
    ) -> tuple[AtomicInteraction, ...]:
        r"""
        Compute the pDOS for a set of atoms (and their associated Wannier functions).

        Parameters
        ----------
        geometry : Structure
            A Pymatgen Structure object with a :code:`"wannier_centres"` site property
            that associates each atom with the indices of its Wannier centres.
        symbols : tuple[str, ...]
            The atomic species to compute the pDOS for. These should match one or more
            of the species present in `geometry`.
        resolve_k : bool, optional
            Whether or not to resolve the pDOS with respect to k-points.

        Returns
        -------
        interactions : tuple[AtomicInteraction, ...]
            A sequence of AtomicInteraction objects, each of which is associated with
            the pDOS for a given atom and its associated Wannier functions.

        See Also
        --------
        get_dos_matrix
        pengwann.geometry.build_geometry

        Notes
        -----
        The k-resolved pDOS for a given Wannier function :math:`\ket{w_{\alpha}}` is
        just the on-site DOS matrix :footcite:p:`WOHP`

        .. math::

            \mathrm{pDOS}_{\alpha}(E, k) = D_{\alpha\alpha}(E, k).

        For `resolve_k` = False, summing over :math:`k` yields the total pDOS for
        :math:`\ket{w_{\alpha}}`

        .. math::

            \mathrm{pDOS}_{\alpha}(E) = \sum_{k} D_{\alpha\alpha}(E, k).

        The total pDOS for a given atom :math:`A` is computed simply by summing over all
        of its associated Wannier functions

        .. math::

            \mathrm{pDOS}_{A}(E) = \sum_{\alpha \in A} \mathrm{pDOS}_{\alpha}(E).

        References
        ----------
        .. footbibliography::
        """
        wannier_centres = geometry.site_properties["wannier_centres"]

        interactions = []
        for idx in range(len(geometry)):
            symbol = geometry[idx].species_string
            if symbol in symbols:
                label = symbol + str(idx - self._num_wann + 1)
                pair_id = (label, label)

                wannier_interactions = []
                for i in wannier_centres[idx]:
                    wannier_interaction = WannierInteraction(
                        i, i, self._bl_0, self._bl_0
                    )

                    wannier_interactions.append(wannier_interaction)

                interaction = AtomicInteraction(pair_id, tuple(wannier_interactions))

                interactions.append(interaction)

        if not interactions:
            raise ValueError(f"No atoms matching symbols in {symbols} found.")

        updated_interactions = self.assign_descriptors(
            interactions,
            calc_wohp=False,
            calc_wobi=False,
            resolve_k=resolve_k,
            n_proc=n_proc,
        )

        return updated_interactions

    def assign_descriptors(
        self,
        interactions: Iterable[AtomicInteraction],
        calc_wohp: bool = True,
        calc_wobi: bool = True,
        resolve_k: bool = False,
        n_proc: int = 4,
    ) -> tuple[AtomicInteraction, ...]:
        r"""
        Compute WOHPs and/or WOBIs for a set of 2-body interactions.

        Parameters
        ----------
        interactions : Iterable[AtomicInteraction]
            A sequence of AtomicInteraction objects specifying the 2-body interactions
            for which to calculate WOHPs and/or WOBIs.
        calc_wohp : bool, optional
            Whether or not to calculate WOHPs for the input `interactions`. Defaults to
            True.
        calc_wobi : bool, optional
            Whether or not to calculate WOBIs for the input `interactions`. Defaults to
            True.
        resolve_k : bool, optional
            Whether or not to resolve the output WOHPs and/or WOBIs with respect to
            k-points. Defaults to False.

        Returns
        -------
        None

        See Also
        --------
        pengwann.geometry.find_interactions
        get_density_matrix_element

        Notes
        -----
        If both `calc_wohp` and `calc_wobi` are False, then the :code:`dos_matrix`
        attribute of each AtomicInteraction and WannierInteraction will still be set.

        The input `interactions` are modified in-place by setting the :code:`wohp`
        and/or :code:`wobi` attributes of each AtomicInteraction (and optionally each
        of its associated WannierInteraction objects).

        The WOHPs and WOBIs for the input `interactions` are computed using shared
        memory parallelism to avoid copying potentially very large arrays (such as the
        full DOS array) between concurrent processes. Even with shared memory, very
        small (low volume -> many k-points) and very large (many electrons -> many
        bands/Wannier functions) systems can be problematic in terms of memory usage
        if the energy resolution is too high.

        For `resolve_k` = True and `calc_wohp` = True, the k-resolved WOHP for a given
        pair of Wannier functions is computed as :footcite:p:`WOHP, pCOHP`

        .. math::

            \mathrm{WOHP}_{\alpha\beta}(E, k) = -H_{\alpha\beta}D_{\alpha\beta}(E, k),

        where :math:`H` is the Wannier Hamiltonian and :math:`D_{\alpha\beta}` is the
        DOS matrix for Wannier functions :math:`\ket{w_{\alpha}}` and
        :math:`\ket{w_{\beta}}`. For `resolve_k` = False, summing over :math:`k` gives
        the total WOHP between :math:`\ket{w_{\alpha}}` and :math:`\ket{w_{\beta}}`

        .. math::

            \mathrm{WOHP}_{\alpha\beta}(E) = -H_{\alpha\beta}\sum_{k} D_{\alpha\beta}
            (E, k).

        Summing over all WOHPs associated with a given pair of
        atoms yields

        .. math::

            \mathrm{WOHP}_{AB}(E) = \sum_{\alpha\beta \in AB}
            \mathrm{WOHP}_{\alpha\beta}(E),

        which is the total WOHP for the interatomic interaction between atoms :math:`A`
        and :math:`B`.

        For `calc_wobi` = True, the WOBI for a pair of Wannier functions or a pair of
        atoms is computed in an identical manner, except that the DOS matrix is
        weighted by the Wannier density matrix rather than the Wannier Hamiltonian
        :footcite:p:`pCOBI`:

        .. math::

            \mathrm{WOBI}_{\alpha\beta}(E) = P_{\alpha\beta}D_{\alpha\beta}(E).

        References
        ----------
        .. footbibliography::
        """
        if calc_wohp:
            if self._h is None:
                raise TypeError

        if calc_wobi:
            if self._occupation_matrix is None:
                raise TypeError

        wannier_interactions = []
        for interaction in interactions:
            for w_interaction in interaction.wannier_interactions:
                if calc_wohp:
                    bl_vector = tuple(
                        [
                            int(component)
                            for component in w_interaction.bl_2 - w_interaction.bl_1
                        ]
                    )
                    h_ij = self._h[bl_vector][w_interaction.i, w_interaction.j].real  # type: ignore[reportOptionalSubscript]
                    w_interaction_with_h = w_interaction._replace(h_ij=h_ij)

                    wannier_interactions.append(w_interaction_with_h)

                else:
                    wannier_interactions.append(w_interaction)

        updated_wannier_interactions = self.parallelise(
            wannier_interactions, calc_wobi, resolve_k, n_proc
        )

        running_count = 0
        updated_interactions = []
        for interaction in interactions:
            associated_wannier_interactions = updated_wannier_interactions[
                running_count : running_count + len(interaction.wannier_interactions)
            ]

            intermediate_interaction = interaction._replace(
                wannier_interactions=associated_wannier_interactions
            )
            updated_interaction = interaction.with_summed_descriptors()

            updated_interactions.append(updated_interaction)
            running_count += len(updated_interaction.wannier_interactions)

        return tuple(updated_interactions)

    def get_density_of_energy(
        self, interactions: tuple[AtomicInteraction, ...]
    ) -> NDArray[np.float64]:
        r"""
        Calculate the density of energy (DOE).

        Parameters
        ----------
        interactions : tuple[AtomicInteraction, ...]
            A sequence of AtomicInteraction objects containing all of the interatomic
            (off-diagonal) WOHPs.

        Returns
        -------
        doe : ndarray[float]
            The density of energy.

        See Also
        --------
        assign_descriptors : Calculate off-diagonal terms.

        Notes
        -----
        The density of energy is calculated as :footcite:p:`DOE`

        .. math::
            \mathrm{DOE}(E) = \sum_{AB}\mathrm{WOHP}_{AB}(E),

        it is the total WOHP of the whole system, including diagonal
        (:math:`A = B`) terms.

        References
        ----------
        .. footbibliography::
        """
        for interaction in interactions:
            if interaction.wohp is None:
                raise TypeError(
                    f"""The WOHP for interaction {interaction.pair_id} has 
                not been computed. This is required to calculate the DOE."""
                )

        wannier_indices = range(self._num_wann)

        diagonal_terms = tuple(
            WannierInteraction(i, i, self._bl_0, self._bl_0) for i in wannier_indices
        )
        diagonal_interaction = (AtomicInteraction(("D1", "D1"), diagonal_terms),)
        updated_diagonal_interaction = self.assign_descriptors(
            diagonal_interaction, calc_wobi=False
        )

        all_interactions = interactions + updated_diagonal_interaction

        doe = sum([interaction.wohp for interaction in all_interactions])  # type: ignore[reportArgumentType]

        return doe

    def get_bwdf(
        self,
        interactions: tuple[AtomicInteraction, ...],
        geometry: Structure,
        r_range: tuple[float, float],
        nbins: int,
    ) -> tuple[NDArray[np.float64], dict[tuple[str, str], NDArray[np.float64]]]:
        """
        Compute one or more bond-weighted distribution functions (BWDFs).

        Parameters
        ----------
        interactions : tuple[AtomicInteraction, ...]
            A sequence of AtomicInteraction obejcts containing all of the necessary
            IWOHPs to weight the RDF/s.
        geometry : Structure
            A Pymatgen Structure object from which to extract interatomic distances.
        r_range : tuple[float, float]
            The range of distances over which to evalute the BWDF/s.
        nbins : int
            The number of bins used to calculate the BWDF/s.

        Returns
        -------
        r : ndarray[float]
            The centre of each distance bin.
        bwdf : dict[tuple[str, str], ndarray[float]]
            A dictionary containing the BWDFs, indexable by the bond species e.g.
            ("Ga", "As") for the Ga-As BWDF.

        See Also
        --------
        assign_descriptors
        integrate_descriptors

        Notes
        -----
        The BWDF is derived from the RDF (radial distribution function). More
        specifically, it is the RDF excluding all interatomic distances that are not
        counted as bonds (as defined by some arbitrary criteria) with the remaining
        distances being weighted by the corresponding IWOHP :footcite:p:`BWDF`.

        References
        ----------
        .. footbibliography::
        """
        distance_matrix = geometry.distance_matrix

        r_min, r_max = r_range
        intervals = np.linspace(r_min, r_max, nbins + 1)
        dr = (r_max - r_min) / nbins
        r = intervals[:-1] + dr / 2

        bonds = []
        bwdf = {}
        for interaction in interactions:
            if interaction.iwohp is None:
                raise TypeError(
                    f"""The IWOHP for interaction {interaction.pair_id} 
                has not been computed. This is required to calculate the BWDF."""
                )

            id_i, id_j = interaction.pair_id
            symbol_i, i = parse_id(id_i)
            symbol_j, j = parse_id(id_j)
            idx_i = i + self._num_wann - 1
            idx_j = j + self._num_wann - 1
            distance = distance_matrix[idx_i, idx_j]

            bond = (symbol_i, symbol_j)
            if bond not in bonds:
                bonds.append(bond)

                bwdf[bond] = np.zeros((nbins))

            for bin_idx, boundary_i, boundary_j in zip(
                range(len(r)), intervals[:-1], intervals[1:], strict=False
            ):
                if boundary_i <= distance < boundary_j:
                    bwdf[bond][bin_idx] += interaction.iwohp
                    break

        return r, bwdf

    def parallelise(
        self,
        wannier_interactions: Iterable[WannierInteraction],
        calc_p_ij: bool,
        resolve_k: bool,
        n_proc: int,
    ) -> tuple[WannierInteraction, ...]:
        memory_keys = ["dos_array", "kpoints", "u"]
        shared_data = [self._dos_array, self._kpoints, self._u]
        if calc_p_ij:
            if self._occupation_matrix is None:
                raise TypeError

            memory_keys.append("occupation_matrix")
            shared_data.append(self._occupation_matrix)

        memory_metadata, memory_handles = allocate_shared_memory(
            memory_keys, shared_data
        )

        args = []
        for w_interaction in wannier_interactions:
            args.append(
                (
                    w_interaction,
                    self._num_wann,
                    self._nspin,
                    calc_p_ij,
                    resolve_k,
                    memory_metadata,
                )
            )

        pool = Pool(processes=n_proc)

        updated_wannier_interactions = tuple(
            tqdm(pool.imap(self._parallel_wrapper, args), total=len(args))
        )

        pool.close()
        for memory_handle in memory_handles:
            memory_handle.unlink()

        return updated_wannier_interactions

    @classmethod
    def _parallel_wrapper(cls, args) -> WannierInteraction:
        """
        A simple wrapper for
        :py:meth:`~pengwann.descriptors.DescriptorCalculator.process_interaction`.

        Parameters
        ----------
        args
            The arguments to be unpacked for
            :py:meth:`~pengwann.descriptors.DescriptorCalculator.process_interaction`.

        Returns
        -------
        wannier_interaction : WannierInteraction
            The input WannierInteraction with the computed properties assigned to the
            relevant attributes.

        Notes
        -----
        This method exists primarily to enable proper :code:`tqdm` functionality with
        :code:`multiprocessing`.
        """
        wannier_interaction = cls._process_interaction(*args)

        return wannier_interaction

    @classmethod
    def _process_interaction(
        cls,
        interaction: WannierInteraction,
        num_wann: int,
        nspin: int,
        calc_wobi: bool,
        resolve_k: bool,
        memory_metadata: dict[str, tuple[tuple[int, ...], np.dtype]],
    ) -> WannierInteraction:
        """
        For a pair of Wannier functions, compute the DOS matrix and (optionally), the
        element of the density matrix required to compute the WOBI.

        Parameters
        ----------
        interaction : WannierInteraction
            The interaction between two Wannier functions for which descriptors are to
            be computed.
        num_wann : int
            The total number of Wannier functions.
        nspin : int
            The number of electrons per fully-occupied band. This should be set to 2
            for non-spin-polarised calculations and set to 1 for spin-polarised
            calculations.
        calc_wobi : bool
            Whether or not to calculate the relevant element of the Wannier density
            matrix for the WOBI.
        resolve_k : bool
            Whether or not to resolve the DOS matrix with respect to k-points.
        memory_metadata : dict[str, tuple[tuple[int, ...], np.dtype]]
            The keys, shapes and dtypes of any data to be pulled from shared memory.

        Returns
        -------
        interaction : WannierInteraction
            The input `interaction` with the computed properties assigned to the
            relevant attributes.
        """
        dcalc_builder: dict[str, Any] = {"num_wann": num_wann, "nspin": nspin}
        memory_handles = []
        for memory_key, metadata in memory_metadata.items():
            shape, dtype = metadata

            shared_memory = SharedMemory(name=memory_key)
            buffered_data = np.ndarray(shape, dtype=dtype, buffer=shared_memory.buf)

            dcalc_builder[memory_key] = buffered_data
            memory_handles.append(shared_memory)

        dcalc = cls(**dcalc_builder)

        c_star = np.conj(dcalc.get_coefficient_matrix(interaction.i, interaction.bl_1))
        c = dcalc.get_coefficient_matrix(interaction.j, interaction.bl_2)

        new_values = {}

        new_values["dos_matrix"] = dcalc.get_dos_matrix(c_star, c, resolve_k)

        if calc_wobi:
            new_values["p_ij"] = dcalc.get_density_matrix_element(c_star, c).real

        for memory_handle in memory_handles:
            memory_handle.close()

        return interaction._replace(**new_values)
