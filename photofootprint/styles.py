"""Default layer styles."""

from qgis.core import (QgsFillSymbol, QgsMarkerSymbol, QgsProperty,
                       QgsSingleSymbolRenderer)

from .compat import symbol_property


def style_photo_layer(layer):
    """Arrow along the heading; a circle for 360 photos or when there is no heading."""
    symbol = QgsMarkerSymbol.createSimple({
        "name": "arrow", "color": "230,85,13", "outline_color": "255,255,255",
        "outline_width": "0.2", "size": "7",
    })
    sl = symbol.symbolLayer(0)
    sl.setDataDefinedProperty(symbol_property("Angle"),
                              QgsProperty.fromExpression('coalesce("heading", 0)'))
    sl.setDataDefinedProperty(symbol_property("FillColor"), QgsProperty.fromExpression(
        "if(\"heading\" is null, '150,150,150', if(\"type\" = '360', '49,130,189', '230,85,13'))"))
    sl.setDataDefinedProperty(symbol_property("Name"), QgsProperty.fromExpression(
        "if(\"heading\" is null or \"type\" = '360', 'circle', 'arrow')"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.setMapTipTemplate(
        '<b>[% "photo_id" %]</b> [% "type" %]<br>'
        'heading [% coalesce(round("heading"), \'unknown\') %]&deg; '
        '([% "heading_source" %])<br>[% "datetime" %]<br>[% "notes" %]'
    )
    layer.triggerRepaint()


def style_footprint_layer(layer, raw=False):
    """Translucent fill for visible footprints; dashed outline only for raw ones."""
    if raw:
        symbol = QgsFillSymbol.createSimple({
            "style": "no", "outline_color": "120,120,120", "outline_style": "dash",
            "outline_width": "0.3",
        })
    else:
        symbol = QgsFillSymbol.createSimple({
            "color": "230,85,13,60", "outline_color": "230,85,13,200", "outline_width": "0.3",
        })
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
