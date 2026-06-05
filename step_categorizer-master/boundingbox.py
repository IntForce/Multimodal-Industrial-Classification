import pathlib
import math

from occwl.viewer import OffscreenRenderer
from occwl.compound import Compound
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.gp import gp_Pnt
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
import OCC.Core.V3d as V3d_TypeOfOrientation

from occwl.solid import Solid
from occwl.compound import Compound
from occwl.viewer import OffscreenRenderer, Viewer

class BoundingBox:
    """
    Class for creating and visualizing bounding boxes, as well as analyzing which faces are visible at given angle renders for CAD solids.
    """
    
    def __init__(self, object_source, color_distance=8, output_dir='output'):
        """
        Initialize the BoundingBox object.
        
        Args:
            solid: Optional solid to initialize with
        """
        # Set the output directory for images
        self.output_dir = pathlib.Path(output_dir)
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.bbox = None
        self.min_point = None
        self.max_point = None
        self.dimensions = None
        self.camera_distance = None
        self.bounding_sphere_radius = None
        self.center = None
        self.color_distance = color_distance
        self.object_source = object_source

        # Load the objects
        self.objects = self.get_objects_to_render(object_source)
        if not self.objects:
            raise ValueError("No valid solid or STEP file provided to BoundingBox.")
        
        # Compute the bounding box and sphere for the object
        self.compute_from_solid(self.objects)

        # Cache for face graph and color maps to ensure consistency across renders
        # We need 2 face maps; one for rendering and one for recognition 
        self._graph = None
        self._color_to_face_map = None
        self._face_to_color_map = None
        self._srgb_color_to_face_map = None

    def set_view(self, heading_deg=0):
        """
        Set the view of the bounding box.
        
        Args:
            heading_deg: Rotation angle in degrees

        Returns:
            Tuple of eye and center points for the camera
        """
        # Get the center of the bounding box
        cx,cy,cz = self.center

        # Compute isometric tilt & heading
        tilt = math.asin(1/math.sqrt(3))
        heading = math.radians(heading_deg)

        R = self.bounding_sphere_radius

        # Build eye‐point offset
        dx = R * math.cos(tilt) * math.cos(heading)
        dy = R * math.cos(tilt) * math.sin(heading)
        dz = R * math.sin(tilt)
        eye = gp_Pnt(cx+dx, cy+dy, cz+dz)
        center = gp_Pnt(cx, cy, cz)

        return eye, center
    
    def get_info(self):
        """
        Get information about the bounding box.
        
        Returns:
            Dictionary with bounding box information
        """
        if self.dimensions is None:
            return {}
        
        return {
            'min_point': self.min_point,
            'max_point': self.max_point,
            'box_x': self.dimensions[0],
            'box_y': self.dimensions[1],
            'box_z': self.dimensions[2],
            'volume': self.dimensions[0] * self.dimensions[1] * self.dimensions[2],
            'center': self.center,
        }

    def compute_from_solid(self, solids):
        """
        Compute the bounding box from a CAD solid.
        
        Args:
            solid: The solid object from OCCWL
        """
        # Create a bounding box
        bbox = Bnd_Box()
        
        # Add the shape to the box
        if isinstance(solids, list):
            # Add each shape to the same bounding box
            for solid in solids:
                brepbndlib.Add(solid._shape, bbox)
        else:
            # Single solid case
            brepbndlib.Add(solids._shape, bbox)
        
        # Get the corners
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        
        # Store the information
        self.bbox = bbox
        self.min_point = (xmin, ymin, zmin)
        self.max_point = (xmax, ymax, zmax)
        self.dimensions = (xmax - xmin, ymax - ymin, zmax - zmin)

        # Calculate the center of the bounding box.
        center_x = (xmin + xmax) / 2.0
        center_y = (ymin + ymax) / 2.0
        center_z = (zmin + zmax) / 2.0
        self.center = (center_x, center_y, center_z)
        
        # The radius is the distance from the center to any corner of the box.
        radius = math.sqrt((xmax - center_x)**2 + (ymax - center_y)**2 + (zmax - center_z)**2)
        self.bounding_sphere_radius = radius

    def create_sphere_shape(self):
        """
        Create a solid sphere shape from the bounding sphere.
        
        Returns:
            Sphere shape that can be rendered.
        """
        
        if self.bounding_sphere_radius is None or self.min_point is None:
            return None
            
        # Get the center of the bounding box
        center_x, center_y, center_z = self.center
        center_pnt = gp_Pnt(center_x, center_y, center_z)
        
        # Create a sphere shape
        sphere_shape = BRepPrimAPI_MakeSphere(center_pnt, self.bounding_sphere_radius).Shape()
        return sphere_shape
    
    def create_box_shape(self):
        """
        Create a solid box shape from the bounding box.
        
        Returns:
            Box shape that can be rendered
        """
        # Set the bounding box dimensions
        xmin, ymin, zmin = self.min_point
        xmax, ymax, zmax = self.max_point
        
        # Create a box shape from the bounding box dimensions
        box_shape = BRepPrimAPI_MakeBox(gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)).Shape()
        
        return box_shape
    
    def load_shapes_from_step(self, step_file):
        """
        Load shapes from a STEP file using low-level OpenCASCADE API.
        This is a fallback method when the standard compound.solids() approach returns no solids.
        
        Args:
            step_file: Path to the STEP file
            
        Returns:
            List of shapes that can be rendered
        """
        
        shapes_to_render = []

        # Create a wrapper class for raw OCC shapes to provide _shape attribute
        class ShapeWrapper:
            def __init__(self, shape):
                self._shape = shape
        
        # Create a STEP reader
        step_reader = STEPControl_Reader()
        status = step_reader.ReadFile(str(step_file))
        
        if status == IFSelect_RetDone:
            # Transfer STEP entities to OpenCASCADE shapes
            step_reader.TransferRoots()
            shape_count = step_reader.NbShapes()
            
            print(f"Found {shape_count} shapes using direct STEP reader")
            
            # Try to convert shapes to OCCWL Solid objects when possible
            for i in range(1, shape_count + 1):
                shape = step_reader.Shape(i)
                if shape and not shape.IsNull():
                    try:
                        # Try to convert to OCCWL Solid
                        solid = Solid(shape)
                        shapes_to_render.append(solid)
                    except Exception:
                        # Use the wrapper to provide _shape attribute
                        print(f"Using shape wrapper for shape {i}")
                        shapes_to_render.append(ShapeWrapper(shape))
        
        return shapes_to_render
    
    def get_objects_to_render(self, object):
        """
        Get a list of objects to render based on the input.
        
        Args:
            object: The object to render, can be a string (file path) or an OCCWL Solid
            
        Returns:
            List of solids to render
        """
        solids_to_render = []
        # Check what the object is and load accordingly
        if isinstance(object, (str, pathlib.Path)):
            compound = Compound.load_from_step(object)
            solids_to_render = list(compound.solids())
            # If no solids found, try loading the shapes directly from the STEP file
            if not solids_to_render:
                try:
                    solids_to_render = self.load_shapes_from_step(object)
                except Exception as e:
                    raise ValueError(f"Failed to load STEP file {object}: {e}")
        else:
            solids_to_render = [object]

        return solids_to_render
    
    def render(self, transparency=0.0, color=(0.3, 0.2, 0.1), edge_color=(0.0, 0.8, 0.4), filename='image.png', width=800, height=600, wireframe=False, heading_deg=45, output_dir=None, no_background=False, disp_axis=True):
        """
        Render the solid with a wireframe bounding box and save as a single image.
        
        Args:
            solid: The solid to render
            edge_color: RGB color tuple for the wireframe (0-1 range)
            filename: Output filename
            width: Image width
            height: Image height
            wireframe: Whether to display the wireframe
            heading_deg: Rotation angle in degrees
            
        Returns:
            Path to the saved image
        """

        # Create renderer with consistent size
        renderer = OffscreenRenderer()
        renderer.enable_antialiasing()
        
        # Set the size, where we subtract the size of the window decorations
        renderer.set_size(width-16, height-39)
        
        #background --- https://autodeskailab.github.io/occwl/api/#occwl.viewer.OffscreenRenderer.set_background_color
        if no_background:
            renderer._display.set_bg_gradient_color([0., 0., 0.], [0., 0., 0.])
            renderer.disable_antialiasing()
        
        # axes --- https://autodeskailab.github.io/occwl/api/#occwl.viewer.Viewer.hide_axes
        if not disp_axis:
            renderer._display.hide_triedron()
        
        # Display the solids
        for i, solid in enumerate(self.objects):
            try:
                if hasattr(solid, '_shape'):
                    # If solid is an OCCWL Solid, use its _shape attribute
                    renderer.display(solid._shape, color=color, transparency=transparency)
                else:
                    renderer.display(solid, color=color, transparency=transparency)
            except Exception as e:
                raise ValueError(f"Failed to display solid {i}: {e}")

        # Create a completely transparent box for fit purposes
        box_shape = self.create_box_shape()

        # Create a wireframe box shape if requested
        if wireframe:
            renderer.display(box_shape, color=edge_color, transparency=1.0)
        
        # Fit to ensure everything is visible
        renderer.fit()

        # Set the camera to fit the bounding sphere
        diameter = self.bounding_sphere_radius * 2.0
        renderer._display.View.Camera().SetScale(diameter)

        # Rotate the view around the Z-axis while looking at the center of the bounding box
        # We do this after the fit to ensure an equal camera distance
        eye, center = self.set_view(heading_deg=heading_deg)
        renderer._display.View.SetEye(eye.X(), eye.Y(), eye.Z())
        renderer._display.View.SetAt(center.X(), center.Y(), center.Z())
        renderer._display.View.SetUp(0.0, 0.0, 1.0)
        
        # Save to a single image
        render_output_dir = self.output_dir
        if output_dir:
            render_output_dir = pathlib.Path(output_dir)      
            render_output_dir.mkdir(parents=True, exist_ok=True)
        image_path = render_output_dir / filename
        renderer.save_image(image_path)
        
        return True
    
    def display(self, transparency=0.0, color=(0.3, 0.2, 0.1), edge_color=(0.0, 0.8, 0.4), width=800, height=600, wireframe=False, heading_deg=45):
        """
        Render the solid with a wireframe bounding box and save as a single image.
        
        Args:
            solid: The solid to render
            edge_color: RGB color tuple for the wireframe (0-1 range)
            filename: Output filename
            width: Image width
            height: Image height
            wireframe: Whether to display the wireframe
            heading_deg: Rotation angle in degrees
            
        Returns:
            Path to the saved image
        """

        # Create renderer with consistent size
        renderer = Viewer()

        # Add viewing angles
        def view_front():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_Ypos)  # Look from front
            renderer.fit()
        
        def view_back():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_Yneg)  # Look from back
            renderer.fit()
        
        def view_left():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_Xpos)  # Look from left
            renderer.fit()
        
        def view_right():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_Xneg)  # Look from right
            renderer.fit()
        
        def view_top():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_Zpos)  # Look from top
            renderer.fit()
        
        def view_bottom():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_Zneg)  # Look from bottom
            renderer.fit()

        def view_reset():
            renderer._display.View.SetProj(V3d_TypeOfOrientation.V3d_XposYposZpos)  # Isometric view
            renderer.fit()

        renderer.add_menu(name="View")
        renderer.add_submenu(menu="View", callback=view_reset)
        renderer.add_submenu(menu="View", callback=view_right)
        renderer.add_submenu(menu="View", callback=view_left)
        renderer.add_submenu(menu="View", callback=view_back)
        renderer.add_submenu(menu="View", callback=view_front)
        renderer.add_submenu(menu="View", callback=view_bottom)
        renderer.add_submenu(menu="View", callback=view_top)
        
        # Set the size, where we subtract the size of the window decorations
        renderer.set_size(width-16, height-39)
        
        # Display the solids
        for i, solid in enumerate(self.objects):
            try:
                if hasattr(solid, '_shape'):
                    # If solid is an OCCWL Solid, use its _shape attribute
                    renderer.display(solid._shape, color=color, transparency=transparency)
                else:
                    renderer.display(solid, color=color, transparency=transparency)
            except Exception as e:
                raise ValueError(f"Failed to display solid {i}: {e}")

        # Create a completely transparent box for fit purposes
        box_shape = self.create_box_shape()

        # Create a wireframe box shape if requested
        if wireframe:
            renderer.display(box_shape, color=edge_color, transparency=1.0)
        
        # Fit to ensure everything is visible
        renderer.fit()

        # Set the camera to fit the bounding sphere
        diameter = self.bounding_sphere_radius * 2.0
        renderer._display.View.Camera().SetScale(diameter)

        # Rotate the view around the Z-axis while looking at the center of the bounding box
        # We do this after the fit to ensure an equal camera distance
        eye, center = self.set_view(heading_deg=heading_deg)
        renderer._display.View.SetEye(eye.X(), eye.Y(), eye.Z())
        renderer._display.View.SetAt(center.X(), center.Y(), center.Z())
        renderer._display.View.SetUp(0.0, 0.0, 1.0)
        
        renderer.show()