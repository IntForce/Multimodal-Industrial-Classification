import colorsys
import hashlib
from occwl.compound import Compound

excluded_columns = ['index', 'filename', 'category', 'color_hash', 'vol_dif', 'solid_vol', 
                    'bbox_vol', 'area', 'volume', 'bbox_x', 'bbox_y', 'bbox_z', 'hash',
                    'edge_irregular', 'edge_bspline', 'edge_circular']
TOLERANCE = 1e-5  # 0.00001 tolerance for floating-point comparisons
TOLERANCE_LOW = 1e-3  # 0.001 tolerance for less strict comparisons
TOLERANCE_LOWEST = 1e-2  # 0.01 tolerance for the least strict comparisons

def get_color(hash_hex):
    """Generate color using HSV for more distinct hues"""
    
    # Use hash to generate HSV values
    hue = int(hash_hex[0:4], 16) / 65535.0  # 0-1 range for full hue spectrum
    saturation = 0.1 + (int(hash_hex[4:6], 16) % 128) / 255.0 * 0.3  # 0.3-0.7 range
    value = 0.9 + (int(hash_hex[6:8], 16) % 128) / 255.0 * 0.1  # 0.8-1.0 range (bright)
    
    # Convert HSV to RGB
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    
    # Convert to 0-255 range
    r = int(r * 255)
    g = int(g * 255) 
    b = int(b * 255)
    
    return f"#{r:02x}{g:02x}{b:02x}"

def get_hash(characteristics):
    """Generate SHA-256 hash from characteristics excluding certain fields."""
    chars_for_color = {k: v for k, v in characteristics.items() if k not in excluded_columns}
    char_key = tuple(sorted(chars_for_color.items()))
    hash_object = hashlib.sha256(str(char_key).encode())
    return hash_object.hexdigest()

def get_solid(file_path):
    # Load the solid, selecting the specified solid
    compound = Compound.load_from_step(file_path)
    solids = list(compound.solids())

    if not solids:
        target_obj = compound
        solids = [target_obj]

    # Use the compound's exact bounding box, so that all solids are included
    box = compound.exact_box()

    # Have to get the face areas before we lose the face objects when we convert to dict
    face_areas = [f.area() for f in compound.faces()]

    # Get solid and bounding box properties using the occwl functions
    solid_info = {
        'faces': compound.num_faces() or 0,
        'edges': compound.num_edges() or 0,
        'shells': compound.num_shells() or 0,
        'solids': len(solids) or 0,
        'vertices': compound.num_vertices() or 0,
        'wires': compound.num_wires() or 0,
        'area': compound.area() or 0.0,
        'volume': compound.volume() or 0.0,
        'box_x': box.x_length() or 0.0,
        'box_y': box.y_length() or 0.0,
        'box_z': box.z_length() or 0.0,
        'facemap': list(compound.faces()) or None,
        'edgemap': list(compound.edges()) or None,
        'face_areas': face_areas or None
    }

    return solids, solid_info

def get_characteristics(step_file, category=None, generate_hashes=True):
    long_edges = 0
    mid_edges = 0
    irregular_edges = 0
    short_edges = 0
    edge_type_circular = 0
    edge_type_bspline = 0
    edge_type_line = 0
    edge_type_curved = 0
    edge_x_aligned = 0
    edge_y_aligned = 0
    edge_z_aligned = 0
    edge_axis_aligned = 0
    edge_none_aligned = 0
    face_type_plane = 0
    face_type_curved = 0
    face_area_mean = 0
    face_area_histogram = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    try:
        solids, solid_info = get_solid(step_file)
    except Exception as e:
        print(f"Error processing {step_file}: {e}")
        return None

    bbox_volume = solid_info['box_x'] * solid_info['box_y'] * solid_info['box_z']
    bbox_shortest = min(solid_info['box_x'], solid_info['box_y'], solid_info['box_z'])
    bbox_longest = max(solid_info['box_x'], solid_info['box_y'], solid_info['box_z'])
    bbox_middle = (solid_info['box_x'] + solid_info['box_y'] + solid_info['box_z']) - bbox_shortest - bbox_longest
    bbox_square = bool(bbox_longest == bbox_middle or bbox_shortest == bbox_middle)
    edges = solid_info['edgemap']
    faces = solid_info['facemap']
    face_areas = solid_info['face_areas']
    edge_tolerance = TOLERANCE_LOW
    cylindrical = bool(has_round_faces(faces))
    perpendicular = bool(has_perpendicular_faces(faces) if not cylindrical else False)
    holed = bool(solid_info['wires'] > solid_info['faces'] and (solid_info['wires'] - solid_info['faces']) / 2 >= 1)
    holes = int((solid_info['wires'] - solid_info['faces']) / 2) if holed else 0
    
    # Calculate face area statistics and histogram
    face_area_mean = sum(face_areas) / len(face_areas) if face_areas else 0.0
    face_area_min = min(face_areas)
    face_area_max = max(face_areas)
    face_area_range = face_area_max - face_area_min
    
    # If all face areas are the same, then face_area_range is zero, so we put them all in the first bin
    for area in face_areas:
        # Calculate which bin this face area falls into (0-9)
        bin_index = min(9, int((area - face_area_min) / face_area_range * 10)) if face_area_range > 0 else 0
        face_area_histogram[bin_index] += 1

    for face in faces:
        face_type = face.surface_type()
        if face_type == "plane":
            face_type_plane += 1
        else:
            face_type_curved += 1

    for edge in edges:
        edge_start = edge.start_vertex().point()
        edge_end = edge.end_vertex().point()
        edge_direction = (edge_end - edge_start)
        edge_dir_x = edge_direction[0]
        edge_dir_y = edge_direction[1]
        edge_dir_z = edge_direction[2]

        if abs(edge_dir_x) > TOLERANCE_LOW and abs(edge_dir_y) < TOLERANCE_LOW and abs(edge_dir_z) < TOLERANCE_LOW:
            edge_x_aligned += 1
            edge_axis_aligned += 1
        elif abs(edge_dir_y) > TOLERANCE_LOW and abs(edge_dir_x) < TOLERANCE_LOW and abs(edge_dir_z) < TOLERANCE_LOW:
            edge_y_aligned += 1
            edge_axis_aligned += 1
        elif abs(edge_dir_z) > TOLERANCE_LOW and abs(edge_dir_x) < TOLERANCE_LOW and abs(edge_dir_y) < TOLERANCE_LOW:
            edge_z_aligned += 1
            edge_axis_aligned += 1
        else:
            edge_none_aligned += 1

        length = edge.length()
        edge_type = edge.curve_type()

        # Count edge types
        if edge_type == "line":
            edge_type_line += 1
        elif edge_type == "bspline":
            edge_type_bspline += 1
            edge_type_curved += 1
        elif edge_type == "circle" or edge_type == "ellipse":
            edge_type_circular += 1
            edge_type_curved += 1

        # Check if the edge matches one of the bbox dimensions within tolerance
        longest_diff = abs(length - bbox_longest) / max(length, bbox_longest)
        middle_diff = abs(length - bbox_middle) / max(length, bbox_middle)
        shortest_diff = abs(length - bbox_shortest) / max(length, bbox_shortest)

        longest_match = longest_diff < edge_tolerance
        middle_match = middle_diff < edge_tolerance
        shortest_match = shortest_diff < edge_tolerance
        
        if shortest_match:
            short_edges += 1
            continue
        # Handle long matches first to handle the bbox_square condition where longest == middle
        if longest_match and length > bbox_shortest:
            long_edges += 1
            continue
        if middle_match and length > bbox_shortest and length < bbox_longest:
            mid_edges += 1
            continue
        if length > bbox_shortest and length < bbox_longest and not middle_match:
            irregular_edges += 1
            continue

    # Use tolerance for volume comparison to handle floating-point precision
    volume_tolerance = TOLERANCE
    volume_difference = abs(bbox_volume - solid_info['volume']) / max(bbox_volume, solid_info['volume'])
    volumes_match = bool(volume_difference < volume_tolerance)

    characteristics = {
        'filename': step_file.name,
        'category': category or "Uncategorized",
        'shells': solid_info['shells'],
        'solids': solid_info['solids'],
        'edges': solid_info['edges'],
        'faces': solid_info['faces'],
        'vertices': solid_info['vertices'],
        'wires': solid_info['wires'],
        'area': solid_info['area'],
        'volume': solid_info['volume'],
        'edge_long': long_edges,
        'edge_mid': mid_edges,
        'edge_irregular': irregular_edges,
        'edge_short': short_edges,
        'edge_circular': edge_type_circular,
        'edge_bspline': edge_type_bspline,
        'edge_line': edge_type_line,
        'edge_curved': edge_type_curved,
        'edge_x_aligned': edge_x_aligned,
        'edge_y_aligned': edge_y_aligned,
        'edge_z_aligned': edge_z_aligned,
        'edge_axis_aligned': edge_axis_aligned,
        'edge_none_aligned': edge_none_aligned,
        'face_planar': face_type_plane,
        'face_curved': face_type_curved,
        'face_area_mean': round(face_area_mean, 4),
        'face_area_min': round(face_area_min, 4),
        'face_area_max': round(face_area_max, 4),
        'face_area_histogram': str(face_area_histogram),
        'bbox_x': round(solid_info['box_x'],4),
        'bbox_y': round(solid_info['box_y'],4),
        'bbox_z': round(solid_info['box_z'],4),
        'bbox_square': bbox_square,
        'bbox_vol': round(bbox_volume,4),
        'solid_vol': round(solid_info['volume'], 4),
        'vol_dif': round(volume_difference*100, 4),
        'round_faces': cylindrical,
        'perpendicular_faces': perpendicular,
        'volumes_match': volumes_match,
        'holed': holed,
        'holes': holes
    }

    # Calculate color hash from characteristics (excluding non-generic fields)
    if generate_hashes:
        characteristics['hash'] = get_hash(characteristics)
        characteristics['color_hash'] = get_color(characteristics['hash'])

    return characteristics, step_file

def has_perpendicular_faces(faces):
    """
    Check if solid has faces that are perpendicular (like a cube/square).

    Returns True if 99.999% of face pairs are perpendicular or parallel.
    """ 
    perpendicular_pairs = 0
    total_pairs = 0
    
    for i, face1 in enumerate(faces):
        for face2 in faces[i+1:]:  
            # Get UV bounds and calculate center point
            uv_bounds1 = face1.uv_bounds()
            uv_bounds2 = face2.uv_bounds()
            
            # Calculate center UV coordinates
            uv1_center = uv_bounds1.center()
            uv2_center = uv_bounds2.center()

            # Calculate angle between normals
            dot_product = abs(face1.normal(uv1_center).dot(face2.normal(uv2_center)))
            dot_product = max(0.0, min(1.0, dot_product))
            
            # Check if faces are perpendicular (dot product = 0) or parallel (dot product = 1)
            if dot_product == 0 or dot_product == 1:
                perpendicular_pairs += 1
            
            total_pairs += 1

    # If no pairs were found, return False
    if total_pairs == 0:
        return False

    # 99.999% of pairs should be perpendicular or parallel
    return bool((perpendicular_pairs / total_pairs) > 1 - TOLERANCE)

def has_round_faces(faces):
    """
    Check if solid has round faces (like a cylinder).
    """
    round_faces = 0
    amount_faces = 0

    for face in faces:
        surface_type = face.surface_type()
        amount_faces += 1

        if surface_type == "cylinder" or surface_type == "torus":
            round_faces += 1

    # 60% of faces should be round
    #print(f"Round faces: {round_faces} out of {len(faces)}")
    return bool((round_faces / amount_faces) >= 0.6)