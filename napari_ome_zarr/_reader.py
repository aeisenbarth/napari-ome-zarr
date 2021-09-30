"""This module is a napari plugin.

It implements the ``napari_get_reader`` hook specification, (to create a reader plugin).
"""


import logging
import warnings
from typing import Any, Callable, Dict, Iterator, List, Optional

from vispy.color import Colormap, get_colormap

from ome_zarr.data import CHANNEL_DIMENSION
from ome_zarr.io import parse_url
from ome_zarr.reader import Label, Node, Reader
from ome_zarr.types import LayerData, PathLike, ReaderFunction

try:
    from napari_plugin_engine import napari_hook_implementation
except ImportError:

    def napari_hook_implementation(
        func: Callable, *args: Any, **kwargs: Any
    ) -> Callable:
        return func


LOGGER = logging.getLogger("napari_ome_zarr.reader")

METADATA_KEYS = ("name", "visible", "contrast_limits", "colormap",
                 "metadata", "affine", "blending")

@napari_hook_implementation
def napari_get_reader(path: PathLike) -> Optional[ReaderFunction]:
    """Returns a reader for supported paths that include IDR ID.

    - URL of the form: https://s3.embassy.ebi.ac.uk/idr/zarr/v0.1/ID.zarr/
    """
    if isinstance(path, list):
        if len(path) > 1:
            warnings.warn("more than one path is not currently supported")
        path = path[0]
    zarr = parse_url(path)
    if zarr:
        reader = Reader(zarr)
        return transform(reader())
    # Ignoring this path
    return None


def transform_properties(props=None):
    """
    Transform properties

    Transform a dict of {label_id : {key: value, key2: value2}}
    with a key for every LABEL
    into a dict of a key for every VALUE, with a list of values for each
    {
        "index": [1381342, 1381343...]
        "omero:roiId": [1381342, 1381343...],
        "omero:shapeId": [1682567, 1682567...]
    }
    """
    if props is None:
        return None

    properties: Dict[str, List] = {}

    # First, create lists for all existing keys...
    for label_id, props_dict in props.items():
        for key in props_dict.keys():
            properties[key] = []

    keys = list(properties.keys())

    properties["index"] = []
    for label_id, props_dict in props.items():
        properties["index"].append(label_id)
        # ...in case some objects don't have all the keys
        for key in keys:
            properties[key].append(props_dict.get(key, None))
    return properties


def transform(nodes: Iterator[Node]) -> Optional[ReaderFunction]:
    def f(*args: Any, **kwargs: Any) -> List[LayerData]:
        results: List[LayerData] = list()

        for node in nodes:
            data: List[Any] = node.data
            metadata: Dict[str, Any] = {}
            if data is None or len(data) < 1:
                LOGGER.debug(f"skipping non-data {node}")
            else:
                LOGGER.debug(f"transforming {node}")
                shape = data[0].shape

                layer_type: str = "image"
                if node.load(Label):
                    layer_type = "labels"
                    for x in METADATA_KEYS:
                        if x in node.metadata:
                            metadata[x] = node.metadata[x]
                    if len(shape) == 5: # dirty fix for issue with napari 0.4.11
                        # a channel axis doesn't make sense in a label image
                        # therefore it isn't extracted into separate layers.
                        # This results in a dimensionality mismatch between image and labels
                        # layers, which propagates through viewer.dims.points through
                        # to layer.corner_pixels.
                        channel_axis = 1
                        dim_mask = [True] * 6
                        dim_mask[channel_axis] = False
                        metadata["affine"] = metadata["affine"][dim_mask, :][:, dim_mask]
                        data=[d[:,0,...] for d in data]
                else:
                    channel_axis = None
                    if "axes" in node.metadata:
                        # version 0.3 or greater. NB: is 'axes' optional?
                        if "c" in node.metadata["axes"]:
                            channel_axis = node.metadata["axes"].index("c")
                    elif shape[CHANNEL_DIMENSION] > 1:
                        # versions of ome-zarr-py before v0.3 support
                        channel_axis = CHANNEL_DIMENSION

                    if channel_axis is not None:
                        # multi-channel; Copy known metadata values
                        metadata["channel_axis"] = channel_axis
                        for x in METADATA_KEYS:
                            if x in node.metadata:
                                metadata[x] = node.metadata[x]
                        if metadata.get("affine") is not None:
                            # Channel dimension will be extracted by Napari and displayed as separate 4D layers.
                            # Thus affine transformation must be reduced by one dimension.
                            dim_mask = [True] * 6
                            dim_mask[channel_axis] = False
                            metadata["affine"] = metadata["affine"][dim_mask, :][:, dim_mask]
                    else:
                        # single channel image, so metadata just needs single items (not lists)
                        for x in METADATA_KEYS:
                            if x in node.metadata:
                                try:
                                    metadata[x] = node.metadata[x][0]
                                except Exception:
                                    pass

                # Handle the removal of vispy requirement from ome-zarr-py
                cms = metadata.get("colormap", [])
                for idx, cm in enumerate(cms):
                    if not isinstance(cm, Colormap):
                        cms[idx] = get_colormap(cm) if isinstance(cm, str) else Colormap(cm)

                properties = transform_properties(node.metadata.get("properties"))
                if properties is not None:
                    metadata["properties"] = properties

                rv: LayerData = (data, metadata, layer_type)
                LOGGER.debug(f"Transformed: {rv}")
                results.append(rv)

        return results

    return f
