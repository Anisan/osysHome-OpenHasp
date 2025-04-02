import json
from http import HTTPStatus
from flask_restx import Namespace, Resource
from app.api.decorators import api_key_required
from app.authentication.handlers import handle_admin_required
from app.api.models import model_404, model_result
from plugins.OpenHasp import OpenHasp
from plugins.OpenHasp.models.Device import HaspDevice
from app.database import row2dict

_api_ns = Namespace(name="OpenHasp",description="OpenHasp namespace",validate=True)

response_result = _api_ns.model('Result', model_result)
response_404 = _api_ns.model('Error', model_404)

_instance: OpenHasp = None

def create_api_ns(instance: OpenHasp):
    global _instance
    _instance = instance
    return _api_ns

@_api_ns.route("/page/<device_id>/<page>", endpoint="openhasp_page")
class OpenPage(Resource):
    @api_key_required
    @handle_admin_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "Result", response_result)
    @_api_ns.response(404, 'Not Found', response_404)
    def get(self, device_id, page):
        '''
        Open page
        '''
        panel = _instance.session.query(HaspDevice).where(HaspDevice.id == device_id).one_or_none()
        if (panel):
            batch = {}
            batch["page"] = page
            _instance.send_batch(panel.mqtt_path, batch)
            return {"success" : True}, 200
        return {"success": False,
                "msg": "Panel not found."}, 404

@_api_ns.route("/panels", endpoint="openhasp_panels")
class GetPanels(Resource):
    @api_key_required
    @handle_admin_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "List panel", response_result)
    def get(self):
        '''
        Get panels
        '''
        panels = _instance.session.query(HaspDevice).all()
        result = [row2dict(panel) for panel in panels]
        for panel in result:
            panel["panel_config"] = json.loads(panel["panel_config"])
        return {"success": True,
                "result": result}, 200
