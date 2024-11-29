"""
Galactic bulge/disk/halo decomposition

"""

import logging

import numpy as np

from .. import array, config, filt, transformation, util
from . import angmom, profile

logger = logging.getLogger('pynbody.analysis.decomp')

def estimate_jcirc_from_energy(h, particles_per_bin=500, quantile=0.99):
    """Estimate the circular angular momentum as a function of energy.

    This routine calculates the circular angular momentum as a function of energy
    for the stars in the simulation, using a profile with a fixed number of particles
    per bin.

    Arguments
    ---------

    h : SimSnap
        The simulation snapshot to analyze

    quantile : float
        The circular angular momentum will be estimated as the specified quantile of the scalar angular momentum

    particles_per_bin : int
        The approximate number of particles per bin in the profile. Default is 500.

    """
    nbins = len(h) // particles_per_bin

    pro_d = profile.QuantileProfile(h, q=(quantile,), nbins=nbins, type='equaln', calc_x = lambda sim : sim['te'])

    pro_d.create_particle_array("j2", particle_name='jcirc2', target_simulation=h)
    h['jcirc'] = np.sqrt(h['jcirc2'])

    return pro_d



def decomp(h, aligned=False, j_disk_min=0.8, j_disk_max=1.1, E_cut=None, j_circ_from_r=False,
           log_interp=False, angmom_size="3 kpc", particles_per_bin = 500):
    """Creates an array 'decomp' for star particles in the simulation, with an integer specifying components.

    The possible values of the components are:

    1 -- thin disk

    2 -- halo

    3 -- bulge

    4 -- thick disk

    5 -- pseudo bulge

    This routine is based on an original IDL procedure by Chris Brook.

    Arguments
    ---------

    h : SimSnap
        The simulation snapshot to analyze

    aligned : bool
        If True, the simulation is assumed to be already aligned so that the disk is in the xy plane.
        Otherwise, the simulation is recentered and aligned into the xy plane.

    j_disk_min : float
        The minimum angular momentum as a proportion of the circular angular momentum which a particle must have to be
        part of the 'disk'.

    j_disk_max : float
        The maximum angular momentum as a proportion of the circular angular momentum which a particle can have to be
        part of the 'disk'.

    E_cut : float
        The energy boundary between bulge and spheroid. If None, this is taken to be the median energy of the stars.

    j_circ_from_r : bool
        If True, the maximum angular momentum is determined as a function of radius, rather than as a function of
        orbital energy. Default False (determine as function of energy).

    angmom_size : str
        The size of the disk to use for calculating the angular momentum vector. Default is "3 kpc".

    particles_per_bin : int
        The approximate number of particles per bin in the profile. Default is 500.

    """

    import scipy.interpolate as interp
    global config

    # Center, eliminate proper motion, rotate so that
    # gas disk is in X-Y plane
    if aligned:
        tx = transformation.NullTransformation(h)
    else:
        tx = angmom.faceon(h, disk_size=angmom_size)

    with tx:

        # Find KE, PE and TE
        ke = h['ke']
        pe = h['phi']

        h['phi'].convert_units(ke.units)  # put PE and TE into same unit system

        te = ke + pe
        h['te'] = te
        te_star = h.star['te']

        te_max = te_star.max()

        # Add an arbitrary offset to the PE to reflect the idea that
        # the group is 'fully bound'.
        te -= te_max
        logger.info("te_max = %.2e" % te_max)

        h['te'] -= te_max

        logger.info("Making disk rotation curve...")

        # Now make a rotation curve for the disk. We'll take everything
        # inside a vertical height of eps*3

        d = h[filt.Disc('1 Mpc', h['eps'].min() * 3)]

        nbins = len(d) // particles_per_bin

        pro_d = profile.Profile(d, nbins=nbins, type='equaln')

        pro_phi = pro_d['phi']
        pro_phi -= te_max

        # (will automatically be reflected in E_circ etc)
        # calculating v_circ for j_circ and E_circ is slow

        if j_circ_from_r:
            pro_d.create_particle_array("j_circ", target_simulation=h)
            pro_d.create_particle_array("E_circ", target_simulation=h)
        else:

            if log_interp:
                j_from_E = interp.interp1d(
                    np.log10(-pro_d['E_circ'].in_units(ke.units))[::-1], np.log10(pro_d['j_circ'])[::-1], bounds_error=False)
                h['j_circ'] = 10 ** j_from_E(np.log10(-h['te']))
            else:
                #            j_from_E  = interp.interp1d(-pro_d['E_circ'][::-1], (pro_d['j_circ'])[::-1], bounds_error=False)
                j_from_E = interp.interp1d(
                    pro_d['E_circ'].in_units(ke.units), pro_d['j_circ'], bounds_error=False)
                h['j_circ'] = j_from_E(h['te'])

            # The next line forces everything close-to-unbound into the
            # spheroid, as per CB's original script ('get rid of weird
            # outputs', it says).
            h['j_circ'][np.where(h['te'] > pro_d['E_circ'].max())] = np.inf

            # There are only a handful of particles falling into the following
            # category:
            h['j_circ'][np.where(h['te'] < pro_d['E_circ'].min())] = pro_d[
                'j_circ'][0]

        h['jz_by_jzcirc'] = h['j'][:, 2] / h['j_circ']
        h_star = h.star

        if 'decomp' not in h_star:
            h_star._create_array('decomp', dtype=int)
        disk = np.where(
            (h_star['jz_by_jzcirc'] > j_disk_min) * (h_star['jz_by_jzcirc'] < j_disk_max))

        h_star['decomp', disk[0]] = 1
        # h_star = h_star[np.where(h_star['decomp']!=1)]

        # Find disk/spheroid angular momentum cut-off to make spheroid
        # rotational velocity exactly zero.

        V = h_star['vcxy']
        JzJcirc = h_star['jz_by_jzcirc']
        te = h_star['te']

        logger.info("Finding spheroid/disk angular momentum boundary...")

        j_crit = util.bisect(0., 5.0, lambda c: np.mean(V[np.where(JzJcirc < c)]))

        logger.info("j_crit = %.2e" % j_crit)

        if j_crit > j_disk_min:
            logger.warning(
                "!! j_crit exceeds j_disk_min. This is usually a sign that something is going wrong (train-wreck galaxy?)")
            logger.warning("!! j_crit will be reset to j_disk_min=%.2e" % j_disk_min)
            j_crit = j_disk_min

        sphere = np.where(h_star['jz_by_jzcirc'] < j_crit)

        if E_cut is None:
            E_cut = np.median(h_star['te'])

        logger.info("E_cut = %.2e" % E_cut)

        halo = np.where((te > E_cut) * (JzJcirc < j_crit))
        bulge = np.where((te <= E_cut) * (JzJcirc < j_crit))
        pbulge = np.where((te <= E_cut) * (JzJcirc > j_crit)
                          * ((JzJcirc < j_disk_min) + (JzJcirc > j_disk_max)))
        thick = np.where((te > E_cut) * (JzJcirc > j_crit)
                         * ((JzJcirc < j_disk_min) + (JzJcirc > j_disk_max)))

        h_star['decomp', halo] = 2
        h_star['decomp', bulge] = 3
        h_star['decomp', thick] = 4
        h_star['decomp', pbulge] = 5

    # Return profile object for informational purposes
    return pro_d
