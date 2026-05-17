from glayout.backend.typings import Component
from pydantic import validate_arguments


@validate_arguments
def component_snap_to_grid(comp: Component) -> Component:
	"""snaps all polygons and ports in component to grid
	comp = the component to snap to grid
	NOTE this function will flatten the component
	"""
	# flatten() already returns a fresh component with copied polygons
	# and add_ports'd port set; the follow-up .copy() the original did
	# was a full second-pass deep copy of every polygon/port for no
	# semantic benefit (snap happens at Port.__init__ time, not at
	# Component.copy time).
	name = comp.name
	comp = comp.flatten()
	comp.name = name
	return comp


