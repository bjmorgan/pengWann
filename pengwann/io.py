import numpy as np
from typing import Optional


def read_U(path: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Read in the unitary matrices U^k_mn that define the Wannier
    functions w_nR from the Kohn-Sham states psi_mk.

    Args:
        path (str): The filepath to seedname_u.mat.

    Returns:
        U (np.ndarray): The unitary matrices U^k.
        kpoints (np.ndarray): The k-points corresponding to each U^k.

    Notes:
        The output array is a num_kpoints x num_bands x num_wann tensor,
        each num_bands x num_wann block is a matrix U^k.
    """
    U_list, kpoints_list = [], []

    with open(path, 'r') as stream:
        lines = stream.readlines()

    num_kpoints, num_wann = [int(string) for string in lines[1].split()[:-1]]

    block_indices = [idx * (num_wann**2 + 2) + 4 for idx in range(num_kpoints)]
    column_indices = [idx * num_wann for idx in range(num_wann)]

    for block_idx in block_indices:
        U_k = []

        kpoint = [float(string) for string in lines[block_idx - 1].split()]
        kpoints_list.append(kpoint)

        for row_idx in range(num_wann):
            row = []

            for column_idx in column_indices:
                element_idx = block_idx + row_idx + column_idx
                real, imaginary = [
                    float(string) for string in lines[element_idx].split()
                ]

                row.append(complex(real, imaginary))

            U_k.append(row)

        U_list.append(U_k)

    U = np.array(U_list)
    kpoints = np.array(kpoints_list)

    return U, kpoints


def read_eigenvalues(
    path: str, num_bands: int, num_kpoints: int, num_wann: Optional[int]=None
) -> np.ndarray:
    """
    Read in the Kohn-Sham eigenvalues.

    Args:
        path (str): The filepath to seedname.eig.
        num_bands (int): The number of bands.
        num_kpoints (int): The number of k-points.
        num_wann (Optional[int]): The number of Wannier functions (only required with
        disentanglement).

    Returns:
        eigenvalues (np.ndarray): The Kohn-Sham eigenvalues.

    Notes:
        The output array is a num_bands x num_kpoints matrix.
    """
    eigenvalues_list = []

    if num_wann is None:
        num_wann = num_bands

    with open(path, 'r') as stream:
        lines = stream.readlines()

    block_indices = [idx * num_bands for idx in range(num_kpoints)]

    for column_idx in range(num_wann):
        row = []

        for block_idx in block_indices:
            eigenvalue = float(lines[column_idx + block_idx].split()[-1])

            row.append(eigenvalue)

        eigenvalues_list.append(row)

    eigenvalues = np.array(eigenvalues_list)

    return eigenvalues


def read_hamiltonian(path: str) -> dict[tuple[int, ...], np.ndarray]:
    """
    Read in the Wannier Hamiltonian.

    Args:
        path (str): The filepath to seedname_hr.dat.

    Returns:
        H (np.ndarray): The Wannier Hamiltonian.

    Notes:
        H is a dictionary with keys corresponding to Bravais lattice
        vectors (in tuple form). Each value is a num_wann x num_wann
        matrix.
    """
    with open(path, 'r') as stream:
        lines = stream.readlines()

    num_wann = int(lines[1])
    num_Rpoints = int(lines[2])

    start_idx = int(np.ceil(num_Rpoints / 15)) + 3

    H = {}

    base_list = [0, 0, 0]
    base_R = tuple(base_list)
    H[base_R] = np.zeros((num_wann, num_wann), dtype=complex)

    for idx in range(3):
        for element in (1, -1):
            R_list = base_list.copy()
            R_list[idx] = element

            R = tuple(R_list)
            H[R] = np.zeros((num_wann, num_wann), dtype=complex)

    for line in lines[start_idx:]:
        data = line.split()
        R = tuple([int(string) for string in data[:3]])

        if R in H.keys():
            m, n = [int(string) - 1 for string in data[3:5]]
            real, imaginary = [float(string) for string in data[5:]]

            H[R][m, n] = complex(real, imaginary)

    return H
