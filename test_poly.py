import sys
import json

# Mimic region_points_from_polygon structure
def test_unpack():
    p = json.loads('{"color": "#0f766e", "points": [{"x": 0.32395, "y": 0.07366}, {"x": 0.87339, "y": 0.07366}, {"x": 0.87339, "y": 0.51772}, {"x": 0.32395, "y": 0.51772}], "polygons": [[{"x": 0.32395, "y": 0.07366}, {"x": 0.87339, "y": 0.07366}, {"x": 0.87339, "y": 0.51772}, {"x": 0.32395, "y": 0.51772}]]}')
    
    sys.path.append('.')
    from app.services.utils.zone_geometry import region_points_from_polygon
    polygons = region_points_from_polygon(p, 1920, 1080)
    print(f"Original from func: {polygons}")

    region_points = []
    if polygons:
        first_poly = polygons[0]
        print(f"First poly: {first_poly}, type: {type(first_poly)}")
        if first_poly and isinstance(first_poly, list) and isinstance(first_poly[0], tuple):
            print("Matched list of tuples")
            region_points = first_poly
        elif first_poly and isinstance(first_poly, tuple):
            print("Matched just tuple")
            region_points = polygons
        else:
             print("Flattening safely")
             for block in polygons:
                 if isinstance(block, list):
                     region_points.extend(block)
                     
    print(f"Result length: {len(region_points)}")
    print(region_points)

test_unpack()
