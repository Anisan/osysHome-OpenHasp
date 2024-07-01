import json
from http import HTTPStatus
from flask_restx import Namespace, Resource
from app.api.decorators import api_key_required, role_required
from plugins.OpenHasp import OpenHasp
from plugins.OpenHasp.models.Device import Device
from app.database import row2dict

_api_ns = Namespace(name="OpenHasp",description="OpenHasp namespace",validate=True)
_instance: OpenHasp = None

def create_api_ns(instance: OpenHasp):
    global _instance
    _instance = instance
    return _api_ns

@_api_ns.route("/page/<device_id>/<page>", endpoint="openhasp_page")
class OpenPage(Resource):
    @api_key_required
    @role_required('admin')
    @_api_ns.doc(security="apikey")
    def get(self, device_id, page):
        '''
        Open page
        '''
        panel = _instance.session.query(Device).where(Device.id == device_id).one_or_none()
        if (panel):
            batch = {}
            batch["page"] = page
            _instance.send_batch(panel.mqtt_path, batch)
            return "ok"
        return "Not found"

@_api_ns.route("/panels", endpoint="openhasp_panels")
class GetPanels(Resource):
    @api_key_required
    @role_required('admin')
    @_api_ns.doc(security="apikey")
    def get(self):
        '''
        Get panels
        '''
        panels = _instance.session.query(Device).all()
        result = [row2dict(panel) for panel in panels]
        for panel in result:
            panel["panel_config"] = json.loads(panel["panel_config"])
        return result, HTTPStatus.OK
