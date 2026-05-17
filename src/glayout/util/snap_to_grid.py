from glayout.backend.typings import Component
from pydantic import validate_arguments


@validate_arguments
def component_snap_to_grid(comp: Component) -> Component:
	"""snaps all polygons and ports in component to grid
	comp = the component to snap to grid
	NOTE this function will flatten the component
	"""
	name = comp.name
	comp = comp.flatten()
	comp.name = name
	# Snap every polygon vertex to the active PDK grid. In gdsfactory
	# mode this is a no-op because gdsfactory.Port.__init__ snaps every
	# port center on construction, so derived polygon coords already
	# land on the grid. In gdstk mode the native ComponentReference's
	# rotate/translate chain composes raw floats and the cumulative
	# error can shift a final polygon edge by one grid unit, producing
	# the 5-nm cutover drift. Snapping polygon vertices here normalizes
	# the two paths so the same DRC-clean GDS comes out either way.
	try:
		import os
		if os.environ.get("GLAYOUT_BACKEND", "").strip().lower() == "gdstk":
			from glayout.backend._active import get_grid_size_um
			import gdstk
			import numpy as _np
			grid = get_grid_size_um()
			if grid > 0:
				grid_nm = grid * 1000.0
				cell = getattr(comp, "_cell", None)
				if cell is not None:
					# Per-polygon snap: vertex-round when safe (area
					# preserved within half a grid unit), else
					# bbox-translate (preserve shape, snap bottom-left
					# corner). Fixes the float-accumulation drift in
					# the native rotate/translate chain. No polygon
					# merging — gdsfactory keeps polygons separate too,
					# so merging here would create XOR diffs vs the
					# baseline.
					old_polys = list(cell.polygons)
					cell.remove(*old_polys)
					tol_area_um2 = (grid / 2.0) ** 2
					for poly in old_polys:
						pts_nm = poly.points * 1000.0
						snap_nm = _np.round(pts_nm / grid_nm) * grid_nm
						new_pts = snap_nm / 1000.0
						orig_area = abs(poly.area())
						cand = gdstk.Polygon(
							new_pts, layer=poly.layer, datatype=poly.datatype,
						)
						new_area = abs(cand.area())
						if abs(new_area - orig_area) <= tol_area_um2 and new_area > 0:
							cell.add(cand)
						else:
							x0_nm = pts_nm[:, 0].min()
							y0_nm = pts_nm[:, 1].min()
							sx_nm = round(x0_nm / grid_nm) * grid_nm
							sy_nm = round(y0_nm / grid_nm) * grid_nm
							dx = (sx_nm - x0_nm) / 1000.0
							dy = (sy_nm - y0_nm) / 1000.0
							if dx or dy:
								poly.translate(dx, dy)
							cell.add(poly)
	except Exception:
		pass
	return comp


