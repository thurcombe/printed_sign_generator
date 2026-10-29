"""Write an OrcaSlicer / Bambu Studio project (.3mf) holding one object made of
several parts, each already assigned to a filament slot, so a two-colour sign
opens in the slicer ready to print. Pure stdlib: binary STL in, zip of XML out."""
import struct, uuid, zipfile, io

NS = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
      'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
      'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"')


def _mesh_xml(stl: bytes, object_id: int) -> str:
    """One <object> with an indexed mesh from a binary STL. Vertices are merged on
    their float32 bit patterns, which is what the exporter wrote, so shared corners
    become one vertex without any tolerance games."""
    n = struct.unpack_from('<I', stl, 80)[0]
    index, verts, tris = {}, [], []
    for t in range(n):
        base = 84 + t * 50 + 12
        ids = []
        for v in range(3):
            key = stl[base + v * 12: base + v * 12 + 12]
            i = index.get(key)
            if i is None:
                i = index[key] = len(verts)
                verts.append(struct.unpack('<fff', key))
            ids.append(i)
        if len(set(ids)) == 3:                      # drop degenerate triangles
            tris.append(ids)
    out = io.StringIO()
    out.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" {NS}>\n'
              f' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n'
              f'  <object id="{object_id}" p:UUID="{uuid.uuid4()}" type="model">\n   <mesh>\n    <vertices>\n')
    for x, y, z in verts:
        out.write(f'     <vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>\n')
    out.write('    </vertices>\n    <triangles>\n')
    for a, b, c in tris:
        out.write(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n')
    out.write('    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n')
    return out.getvalue()


def orca_project(parts, name='custom_sign') -> bytes:
    """parts: list of (part_name, binary_stl_bytes, filament_slot starting at 1).
    Returns the .3mf file as bytes."""
    obj_id = len(parts) + 1
    model = (f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" {NS}>\n'
             f' <metadata name="Application">OrcaSlicer-2.4.2</metadata>\n'
             f' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
             f' <metadata name="Title">{name}</metadata>\n <resources>\n'
             f'  <object id="{obj_id}" p:UUID="{uuid.uuid4()}" type="model">\n   <components>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n')
    settings = (f'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="{obj_id}">\n'
                f'    <metadata key="name" value="{name}"/>\n    <metadata key="extruder" value="1"/>\n')
    files = {}
    for i, (pname, stl, slot) in enumerate(parts, start=1):
        path = f'/3D/Objects/{pname}.model'
        files[path.lstrip('/')] = _mesh_xml(stl, i)
        model += (f'    <component p:path="{path}" objectid="{i}" p:UUID="{uuid.uuid4()}" '
                  f'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n')
        rels += (f' <Relationship Target="{path}" Id="rel-{i}" '
                 f'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n')
        settings += (f'    <part id="{i}" subtype="normal_part">\n      <metadata key="name" value="{pname}"/>\n'
                     f'      <metadata key="extruder" value="{slot}"/>\n'
                     f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n    </part>\n')
    model += (f'   </components>\n  </object>\n </resources>\n <build p:UUID="{uuid.uuid4()}">\n'
              f'  <item objectid="{obj_id}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/>\n'
              f' </build>\n</model>\n')
    rels += '</Relationships>\n'
    settings += (f'  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n    <metadata key="plater_name" value=""/>\n'
                 f'    <metadata key="locked" value="false"/>\n    <model_instance>\n      <metadata key="object_id" value="{obj_id}"/>\n'
                 f'      <metadata key="instance_id" value="0"/>\n      <metadata key="identify_id" value="1"/>\n'
                 f'    </model_instance>\n  </plate>\n  <assemble>\n  </assemble>\n</config>\n')
    files.update({
        '[Content_Types].xml': ('<?xml version="1.0" encoding="UTF-8"?>\n'
                                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                                ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                                ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
                                '</Types>\n'),
        '_rels/.rels': ('<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
                        '</Relationships>\n'),
        '3D/3dmodel.model': model,
        '3D/_rels/3dmodel.model.rels': rels,
        'Metadata/model_settings.config': settings,
    })
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, text in files.items():
            z.writestr(path, text)
    return buf.getvalue()
