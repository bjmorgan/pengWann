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

from pengwann.io import read, read_eigenvalues, read_hamiltonian, read_u


def test_read_eigenvalues(shared_datadir, ndarrays_regression) -> None:
    num_bands = 12
    num_kpoints = 4096

    eigenvalues = read_eigenvalues(
        f"{shared_datadir}/wannier90.eig", num_bands, num_kpoints
    )

    ndarrays_regression.check(
        {"eigenvalues": eigenvalues}, default_tolerance={"atol": 0, "rtol": 1e-07}
    )


def test_read_u(shared_datadir, ndarrays_regression) -> None:
    u, kpoints = read_u(f"{shared_datadir}/wannier90_u.mat")

    ndarrays_regression.check(
        {"U": u, "kpoints": kpoints}, default_tolerance={"atol": 0, "rtol": 1e-07}
    )


def test_read_hamiltonian(shared_datadir, ndarrays_regression) -> None:
    test_h = read_hamiltonian(f"{shared_datadir}/wannier90_hr.dat")

    for R, matrix in test_h.items():
        assert matrix.shape == (8, 8)

    h_000 = test_h[(0, 0, 0)]

    ndarrays_regression.check(
        {"H_000": h_000}, default_tolerance={"atol": 0, "rtol": 1e-07}
    )


def test_read_u_dis(shared_datadir, ndarrays_regression) -> None:
    _, _, u, _ = read("wannier90", f"{shared_datadir}")

    ndarrays_regression.check({"U": u}, default_tolerance={"atol": 0, "rtol": 1e-07})
