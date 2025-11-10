"""
Premiere Pro XML (XMEML v5) generator.

Exports clips in format compatible with Premiere Pro CC 2020+.
"""
import logging
from pathlib import Path
from typing import List, Dict, Any
import xml.etree.ElementTree as ET
from xml.dom import minidom

logger = logging.getLogger(__name__)


def generate_premiere_xml(
    clips: List[Dict[str, Any]],
    output_path: str,
    project_name: str = "SupoClip Export"
) -> str:
    """
    Generate Premiere Pro XML file from clips.

    Args:
        clips: List of clip dicts with path, start_time, end_time, etc.
        output_path: Where to save the XML file
        project_name: Name for the Premiere project

    Returns:
        Path to generated XML file
    """
    logger.info(f"Generating Premiere XML for {len(clips)} clips")

    # Create root element
    xmeml = ET.Element('xmeml', version="5")

    # Create project
    project = ET.SubElement(xmeml, 'project')
    ET.SubElement(project, 'name').text = project_name

    # Create children
    children = ET.SubElement(project, 'children')

    # Create a bin for all clips
    bin_elem = ET.SubElement(children, 'bin')
    ET.SubElement(bin_elem, 'name').text = 'Council Selected Clips'

    bin_children = ET.SubElement(bin_elem, 'children')

    # Add each clip as a clip item in the bin
    for i, clip in enumerate(clips):
        clip_elem = ET.SubElement(bin_children, 'clip', id=f"clip-{i+1}")

        # Clip metadata
        ET.SubElement(clip_elem, 'name').text = clip.get('title', f"Clip {i+1}")
        ET.SubElement(clip_elem, 'duration').text = str(int(clip.get('duration', 30) * 30))  # frames

        # Rate (framerate)
        rate = ET.SubElement(clip_elem, 'rate')
        ET.SubElement(rate, 'timebase').text = '30'
        ET.SubElement(rate, 'ntsc').text = 'FALSE'

        # Media
        media = ET.SubElement(clip_elem, 'media')

        # Video
        video = ET.SubElement(media, 'video')
        video_track = ET.SubElement(video, 'track')

        # File reference
        file_elem = ET.SubElement(clip_elem, 'file', id=f"file-{i+1}")
        ET.SubElement(file_elem, 'name').text = Path(clip['file_path']).name
        ET.SubElement(file_elem, 'pathurl').text = f"file://localhost{clip['file_path']}"

        # Duration
        ET.SubElement(file_elem, 'duration').text = str(int(clip.get('duration', 30) * 30))

        # Rate
        file_rate = ET.SubElement(file_elem, 'rate')
        ET.SubElement(file_rate, 'timebase').text = '30'
        ET.SubElement(file_rate, 'ntsc').text = 'FALSE'

        # Media within file
        file_media = ET.SubElement(file_elem, 'media')
        file_video = ET.SubElement(file_media, 'video')

        # Video characteristics
        video_char = ET.SubElement(file_video, 'samplecharacteristics')
        ET.SubElement(video_char, 'width').text = '1920'
        ET.SubElement(video_char, 'height').text = '1080'

        # Markers for metadata
        markers = ET.SubElement(clip_elem, 'markers')
        marker = ET.SubElement(markers, 'marker')
        ET.SubElement(marker, 'comment').text = f"Score: {clip.get('relevance_score', 0):.2f} | {clip.get('reasoning', '')}"
        ET.SubElement(marker, 'in').text = '0'
        ET.SubElement(marker, 'out').text = '90'  # 3 seconds at 30fps

    # Create sequence for all clips
    sequence = ET.SubElement(children, 'sequence', id="sequence-1")
    ET.SubElement(sequence, 'name').text = 'Council Clips Sequence'

    # Sequence settings
    seq_rate = ET.SubElement(sequence, 'rate')
    ET.SubElement(seq_rate, 'timebase').text = '30'
    ET.SubElement(seq_rate, 'ntsc').text = 'FALSE'

    # Media in sequence
    seq_media = ET.SubElement(sequence, 'media')

    # Video track
    seq_video = ET.SubElement(seq_media, 'video')
    seq_video_track = ET.SubElement(seq_video, 'track')

    # Add clips to timeline with 1-minute gaps
    current_time = 0
    for i, clip in enumerate(clips):
        clip_item = ET.SubElement(seq_video_track, 'clipitem', id=f"clipitem-{i+1}")

        ET.SubElement(clip_item, 'name').text = clip.get('title', f"Clip {i+1}")
        ET.SubElement(clip_item, 'start').text = str(current_time)

        duration_frames = int(clip.get('duration', 30) * 30)
        ET.SubElement(clip_item, 'end').text = str(current_time + duration_frames)
        ET.SubElement(clip_item, 'in').text = '0'
        ET.SubElement(clip_item, 'out').text = str(duration_frames)

        # File reference
        clip_file = ET.SubElement(clip_item, 'file', id=f"file-{i+1}")
        ET.SubElement(clip_file, 'name').text = Path(clip['file_path']).name
        ET.SubElement(clip_file, 'pathurl').text = f"file://localhost{clip['file_path']}"

        # Move to next clip (with 1-minute gap)
        current_time += duration_frames + 1800  # 1 minute = 1800 frames at 30fps

    # Convert to pretty XML
    xml_str = ET.tostring(xmeml, encoding='utf-8')
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent="  ", encoding='utf-8').decode('utf-8')

    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(pretty_xml)

    logger.info(f"✅ Premiere XML generated: {output_path}")

    return output_path
