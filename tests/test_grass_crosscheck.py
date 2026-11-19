"""Cross-check the plugin's visibility against GRASS r.viewshed, an independent,
trusted implementation (exact sweep-line line of sight). Skipped without GRASS."""

import os
import shutil
import subprocess

import numpy as np
import pytest
from osgeo import gdal

from photofootprint.terrain.visibility import visible_cells

from test_footprint import X0, Y0, make_dem
from test_visibility import BUILDINGS, hills

pytestmark = pytest.mark.skipif(shutil.which("grass") is None, reason="GRASS GIS not installed")

EYE, TARGET, R = 1.6, 0.1, 250.0


def r_viewshed(dem, tmp_path):
    tif, out = str(tmp_path / "dem.tif"), str(tmp_path / "vs.tif")
    gdal.Translate(tif, dem.ds, format="GTiff")
    script = tmp_path / "run.sh"
    script.write_text(
        f"r.in.gdal -o input={tif} output=dem --quiet\n"
        "g.region raster=dem --quiet\n"
        f"r.viewshed -b input=dem output=vs coordinates={X0},{Y0} observer_elevation={EYE} "
        f"target_elevation={TARGET} max_distance={R} --quiet\n"
        f"r.out.gdal input=vs output={out} format=GTiff --quiet\n")
    env = dict(os.environ, GRASS_SKIP_MAPSET_OWNER_CHECK="1")
    # --tmp-location (not --tmp-project) works on GRASS 7.8 to 8.4
    subprocess.run(["grass", "--tmp-location", "EPSG:27700", "--exec", "bash", str(script)],
                   check=True, capture_output=True, env=env, timeout=300)
    ds = gdal.Open(out)
    return ds.GetRasterBand(1).ReadAsArray() == 1, ds.GetGeoTransform()


@pytest.mark.parametrize("make", [lambda: make_dem(buildings=BUILDINGS), hills], ids=["buildings", "hills"])
def test_matches_grass_r_viewshed(make, tmp_path):
    dem = make()
    win = dem.window(X0, Y0, R)
    ground = win.value_at(X0, Y0)
    mine = visible_cells(win, X0, Y0, ground + EYE, R)
    theirs, gt = r_viewshed(dem, tmp_path)

    xs, ys = win.cell_centres()
    gx, gy = np.meshgrid(xs, ys)
    dist = np.hypot(gx - X0, gy - Y0)
    zone = (dist > 3) & (dist < R - 2)      # skip the camera's own cells and the range edge
    c = np.floor((gx - gt[0]) / gt[1]).astype(int)
    r = np.floor((gy - gt[3]) / gt[5]).astype(int)
    theirs_on_ours = theirs[r[zone], c[zone]]
    ours = mine[zone]
    n = zone.sum()
    only_grass = np.sum(theirs_on_ours & ~ours) / n
    only_ours = np.sum(ours & ~theirs_on_ours) / n
    print(f"cells {n}: visible only to GRASS {only_grass:.2%}, only to plugin {only_ours:.2%}")
    assert 0.2 < theirs_on_ours.mean() < 0.9      # a real mix of seen and hidden
    assert only_grass < 0.01
    assert only_ours < 0.01
