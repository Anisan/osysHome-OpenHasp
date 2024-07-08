import time
from jinja2 import Environment
import datetime
import json, re, os
import paho.mqtt.client as mqtt
from flask import redirect
from sqlalchemy import or_
from app.database import getSession
from app.core.main.BasePlugin import BasePlugin
from plugins.OpenHasp.models.Device import Device
from plugins.OpenHasp.forms.SettingForms import SettingsForm
from plugins.OpenHasp.forms.DeviceForm import routeDevice
from app.core.lib.object import *
from app.core.lib.common import addNotify, CategoryNotify
from app.authentication.handlers import handle_admin_required
from app.api import api

class OpenHasp(BasePlugin):

    def __init__(self,app):
        super().__init__(app,__name__)
        self.title = "OpenHasp"
        self.version = 1
        self.description = """Support OpenHasp devices"""
        self.category = "Devices"
        self.actions = ['cycle','search']
        self._client = None

        self.session = getSession()

        from plugins.OpenHasp.api import create_api_ns
        api_ns = create_api_ns(self)
        api.add_namespace(api_ns, path="/OpenHasp")
        

    def initialization(self):
        # Создаем клиент MQTT
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        # Назначаем функции обратного вызова
        self._client.on_connect = self.on_connect
        self._client.on_disconnect = self.on_disconnect
        self._client.on_message = self.on_message
        
        if "host" in self.config:
            # Подключаемся к брокеру MQTT
            self._client.connect(self.config.get("host",""), 1883, 0)
            # Запускаем цикл обработки сообщений в отдельном потоке
            self._client.loop_start()

    def admin(self, request):
        op = request.args.get('op', '')
        id = request.args.get('device', '')
            
        if op == 'delete':
            #delete 
            sql = delete(Device).where(Device.id == id)
            self.session.execute(sql)
            self.session.commit()
            return redirect(self.name)

        if op == 'add' or op == 'edit':
            return routeDevice(request)
        
        if op == "reloadpage":
            panel= self.session.query(Device).where(Device.id == id).one_or_none()
            if panel:
                self.reload_pages(panel)
            return redirect(self.name)
 
        settings = SettingsForm()
        if request.method == 'GET':
            settings.host.data = self.config.get('host','')
            settings.port.data = self.config.get('port',1883)
            settings.topic.data = self.config.get('topic','')
        else:
            if settings.validate_on_submit():
                self.config["host"] = settings.host.data
                self.config["port"] = settings.port.data
                self.config["topic"] = settings.topic.data
                self.saveConfig()
                return redirect(self.name)

        devices = self.session.query(Device).all()

        content = {
                "form": settings,
                "devices": devices,
        }
            
        return self.render("openhasp_devices.html", content)
    
    def cyclic_task(self):
        if self.event.is_set():
            # Останавливаем цикл обработки сообщений
            self._client.loop_stop()
            # Отключаемся от брокера MQTT
            self._client.disconnect()
        else:
            time.sleep(1)

    def send_mqtt_command(self, topic, value, qos = 0, retain = False):
        self.logger.info("Publish: %s %s",topic,value)
        self._client.publish(topic, str(value), qos=qos, retain= retain)

    def send_command(self, root_path, command):
        topic = f"{root_path}/command"
        self.send_mqtt_command(topic, command)

    def send_value(self, root_path, key, value):
        topic = f"{root_path}/command/{key}"
        self.send_mqtt_command(topic, value)

    def send_batch(self, root_path, batch):
        keys = list(batch.keys())
        if len(keys) == 1:
            key = keys[0]
            self.send_value(root_path, key, batch[key])
            return
        
        data = [f"{key} {batch[key]}" for key in keys]
        command = "json "+json.dumps(data)
        self.send_command(root_path, command)

    def changeLinkedProperty(self, obj, prop, value):
        self.logger.debug("PropertySetHandle: %s.%s=%s",obj,prop,value)
        op = f"%{obj}.{prop}%"
        found = self.update_values(0, "", op, value)
        if not found:
            from app.core.lib.object import removeLinkFromObject
            removeLinkFromObject(obj, prop, self.name)

    # Функция обратного вызова для подключения к брокеру MQTT
    def on_connect(self,client, userdata, flags, rc):
        self.logger.info("Connected with result code %s",rc)
        # Подписываемся на топик
        if self.config["topic"]:
            topics = self.config["topic"].split(',')
            for topic in topics:
                self._client.subscribe(topic)

    def on_disconnect(self, client, userdata, rc):
        addNotify("Disconnect MQTT",str(rc),CategoryNotify.Error,self.name)
        if rc == 0:
            self.logger.info("Disconnected gracefully.")
        elif rc == 1:
            self.logger.info("Client requested disconnection.")
        elif rc == 2:
            self.logger.info("Broker disconnected the client unexpectedly.")
        elif rc == 3:
            self.logger.info("Client exceeded timeout for inactivity.")
        elif rc == 4:
            self.logger.info("Broker closed the connection.")
        else:
            self.logger.warning("Unexpected disconnection with code: %s", rc)

    # Функция обратного вызова для получения сообщений
    def on_message(self,client, userdata, msg):
        #self.logger.info(msg.topic+" "+str(msg.payload))
        payload = msg.payload.decode('utf-8')
        
        if re.search(r'command', msg.topic):
            return

        if not payload:
            return False

        self.processMessage(msg.topic, payload)

    def processMessage(self, topic, msg):
        
        self.logger.debug(f'Topic {topic} = {msg}')

        if re.search(r'discovery', topic):
            discovery = json.loads(msg)
            if "node_t" not in discovery:
                return
            mqtt_path = discovery["node_t"][:-1]
            device = self.session.query(Device).where(Device.mqtt_path == mqtt_path).one_or_none()
            if not device:
                device = Device(mqtt_path=mqtt_path, title = discovery["node"])
                self.session.add(device)
                self.session.commit()
            return
        
        devices = self.session.query(Device).all()
        for device in devices:
            if device.mqtt_path in topic:
                try:
                    self.process_panel_message(device, topic, msg)
                except Exception as e:
                    self.logger.error(f'Error processing message: {e}')
                return

    def process_panel_message(self, panel: Device, topic, msg):
        key = os.path.basename(topic)
        
        self.logger.debug(f"Processing ({panel.title}) {topic} - {key}: {msg}")
        
        if key == "page":
            panel.current_page = msg
            self.session.commit()
            self.set_linked_property(panel, "page", msg)
            config = json.loads(panel.panel_config)
            if "page_linkedProperty" in config:
                self.update_values(panel.id, "page", config["page_linkedProperty"], msg)
        elif key == "LWT":
            online = True if msg=='online' else False
            if panel.online != online:
                panel.online = online
                self.session.commit()
                self.set_linked_property(panel, "LWT", msg)
        elif key == "statusupdate":
            value = json.loads(msg)
            if value["uptime"] < 30:
                self.reload_pages(panel)
            panel.ip = value["ip"]
            self.session.commit()
            self.set_linked_property(panel, "ip", value["ip"])
        elif key == "idle":
            res = self.set_linked_property(panel, "idle", msg)
            if not res:
                if msg == "long":
                    self.send_value(panel.mqtt_path, "backlight", 0)
                    obj = {"page": 0, "id": 99, "obj": "obj", "x": 0, "y": 0, "w": 480, "h": 480, "radius": 0, "hidden": 0, "bg_grad_dir": 0, "bg_color": "black", "border_width": 0}
                    jsonl = f"jsonl {json.dumps(obj)}"
                    self.send_command(panel.mqtt_path, jsonl)
                elif msg == "off":
                    self.send_command(panel.mqtt_path, "p0b99.delete")
                    self.send_value(panel.mqtt_path, "backlight", 255)
        elif key == "backlight":
            backlight = json.loads(msg)
            self.set_linked_property(panel, "brightness", backlight['brightness'])
            self.set_linked_property(panel, "backlight", backlight['state'])
        elif "output" in key:
            value = json.loads(msg)
            state = 1 if value["state"] == "on" else 0
            self.set_linked_property(panel, key, state)
        else:
            match = re.match(r'^p(\d+)b(\d+)$', key)
            if match:
                page_index, object_id = match.groups()
                event = json.loads(msg)
                config = json.loads(panel.panel_config)
                if int(page_index) > len(config["pages"]) - 1:
                    return
                page = config["pages"][int(page_index)]
                obj = None
                if "tag" in event:
                    for template_obj in config['templates'][event['tag']['template']]:
                        if template_obj['id'] == event['tag']['id']:
                            obj = template_obj
                            if 'parent' in event['tag']:
                                for page_obj in page["objects"]:
                                    if page_obj["id"] == event['tag']['parent']:
                                        self.merge_objects(obj, page_obj)
                                        break
                            for k, v in obj.items():
                                v = str(v)
                                obj[k] = self.replace_object(event["tag"]["object"], v)
                                if v.startswith('.'):
                                    obj[k] = event["tag"]["object"] + v
                            break
                else:
                    for page_obj in page['objects']:
                        if page_obj["id"] == object_id:
                            obj = page_obj
                            break

                if obj is None:
                    cache_name = f"d{panel.id}p{page_index}b{object_id}"
                    cache = self.check_from_cache(f"hasp:{cache_name}")
                    if cache:
                        obj = json.loads(cache)

                self.logger.debug(json.dumps(obj))

                if obj:
                    event.update({"object": key, "page": int(page_index), "id": obj["id"]})
                    if event['event'] + "_linkedTemplate" in obj and 'linkedObject' in obj:
                        self.open_template(panel, obj[event['event'] + "_linkedTemplate"], obj["linkedObject"])
                        return
                    if event['event'] + "_command" in obj:
                        cmd = obj[event['event'] + "_command"]
                        if cmd == 'delete':
                            self.send_command(panel.mqtt_path, f"p{page_index}b{object_id}.delete")
                        elif cmd == 'close':
                            self.close_template(panel, event['tag']['template'])
                    if event['event'] + "_linkedMethod" in obj:
                        callMethodThread(obj[event['event'] + "_linkedMethod"], {'event': event})
                        return
                    default_event = config.get("value_event", "up")
                    if obj["obj"] in ['dropdown', 'roller']:
                        default_event = "changed"
                    if event["event"] == default_event:
                        if "val" in event and "val" in obj:
                            self.set_value(obj["val"], event["val"])
                            self.update_values(panel.id, f"{key}.val", obj["val"], event["val"])
                        if "text" in event and "text" in obj:
                            self.set_value(obj["text"], event["text"])
                            self.update_values(panel.id, f"{key}.text", obj["val"], event["val"])
                        if "color" in event and "color" in obj:
                            self.set_value(obj["color"], event["color"])
                            self.update_values(panel.id, f"{key}.color", obj["val"], event["val"])

    def clean_object(self, obj):
        events = ["up", "down", "release", "long", "hold", "changed"]
        for event in events:
            obj.pop(f"{event}_linkedMethod", None)
            obj.pop(f"{event}_linkedScript", None)
            obj.pop(f"{event}_linkedTemplate", None)
            obj.pop(f"{event}_command", None)
        obj.pop("linkedObject", None)

    def reload_page(self, panel: Device, index, clean=True):
        if clean:
            self.send_command(panel.mqtt_path, f"clearpage {index}")

        config = json.loads(panel.panel_config)
        page = config["pages"][index]
        page_atr = {"page": index}

        if "comment" in page:
            page_atr["comment"] = page["comment"]
        if "back" in page:
            page_atr["back"] = page["back"]
        if "next" in page:
            page_atr["next"] = page["next"]
        if "prev" in page:
            page_atr["prev"] = page["prev"]

        jsonl = f'jsonl {json.dumps(page_atr)}'
        self.send_command(panel.mqtt_path, jsonl)

        for obj in page['objects']:
            if obj["obj"] != "template":
                for key, val in obj.items():
                    if isinstance(val, str):
                        obj[key] = self.process_value(val, "", "")
                
                self.clean_object(obj)
                jsonl = f'jsonl {json.dumps(obj)}'
                self.send_command(panel.mqtt_path, jsonl)
            elif "template" in obj:
                self.add_template(panel, obj)

    def reload_pages(self, panel:Device, clean=True):
        config = json.loads(panel.panel_config)
        for index in range(len(config["pages"])):
            self.reload_page(panel, index, clean)

    def reload_panels(self):
        self.logger.info("Reload panel's config")
        panels = self.session.query(Device).all()
        for panel in panels:
            self.reload_pages(panel)

    def add_template(self, panel: Device, parent):
        name = parent["template"]
        config = json.loads(panel.panel_config)
        if 'templates' not in config or name not in config['templates']:
            return
        
        template = config['templates'][name]
        self.merge_objects(template[0], parent)
        
        for index, obj in enumerate(template):
            if 'tag' not in obj:
                tag = {
                    "object": parent["linkedObject"],
                    "template": name,
                    "id": obj["id"],
                    "parent": parent["id"]
                }
                obj['tag'] = tag
            
            obj["id"] = int(parent["id"]) + int(obj["id"])
            
            if index > 0 and "parentid" in obj:
                obj["parentid"] = int(obj["parentid"]) + int(parent["id"])
            
            for key, val in obj.items():
                if not isinstance(val, str):
                    continue
                if val == '%.description%':
                    o = getObject(parent["linkedObject"])
                    if o:
                        obj[key] = o.description
                    else:
                        obj[key] = 'nil'
                elif val == '%.name%':
                    obj[key] = parent["linkedObject"]
                else:
                    op = self.replace_object(parent["linkedObject"], val)
                    obj[key] = self.process_value(op, "", "")
            
            self.clean_object(obj)
            jsonl = "jsonl " + json.dumps(obj)
            self.send_command(panel.mqtt_path, jsonl)

    def replace_object(self, obj, property_):
        pattern = r'%\.([^\%]*)%'
        def callback(matches):
            return f'%{obj}.{matches.group(1)}%'
        return re.sub(pattern, callback, str(property_))

    def merge_objects(self, child, parent):
        ignore = ["id", "obj", "template"]
        for key, val in parent.items():
            if key not in ignore:
                child[key] = val

    def open_template(self, panel: Device, name, ob):
        config = json.loads(panel.panel_config)
        if 'templates' not in config or name not in config['templates']:
            return
        
        template = config['templates'][name]
        for obj in template:
            if 'tag' not in obj:
                tag = {
                    "object": ob,
                    "template": name,
                    "id": obj['id']
                }
                obj['tag'] = tag
            
            obj['page'] = panel.current_page
            
            for key, val in obj.items():
                if not isinstance(val, str):
                    continue
                if val == '%.description%':
                    o = getObject(ob)
                    obj[key] = o.description
                elif val == '%.name%':
                    obj[key] = tag
                else:
                    op = self.replace_object(ob, val)
                    obj[key] = self.process_value(op, "", "")
            
            self.clean_object(obj)
            jsonl = "jsonl " + json.dumps(obj)
            self.send_command(panel.mqtt_path, jsonl)

    def close_template(self, panel:Device, name):
        config = json.loads(panel.panel_config)
        if 'templates' not in config or name not in config['templates']:
            return
        
        template = config['templates'][name]
        for obj in template:
            cmd = f"p{panel.current_page}b{obj['id']}.delete"
            self.send_command(panel.mqtt_path, cmd)

    def set_value(self, op, val):
        pattern = r'%([^%]+)\.([^%]+)%'
        match = re.search(pattern, op)
        if match:
            key = f"{match.group(1)}.{match.group(2)}"
            updateProperty(key, val, self.name)
            return True
        return False

    def set_linked_property(self, panel: Device, name, value):
        if not panel.panel_config:
            return
        config = json.loads(panel.panel_config)
        linked_property = config.get(f"{name}_linkedProperty")
        if linked_property:
            return self.set_value(linked_property, value)
        return False

    def update_values(self, panel_id, name_value, op, value):
        found = 0
        cache = self.check_from_cache(f"hasp:{op}")
        if cache:
            cache = json.loads(cache)

            for key, device in cache.items():
                batch = device["batch"]
                for obj, val in batch.items():
                    data = self.process_value(val, op, value)
                    batch[obj] = data
                if key == panel_id:
                    if name_value:
                        batch.pop(name_value, None)
                if batch:
                    found = True
                    self.send_batch(device['MQTT'], batch)
            if found:
                return found

        cache = {}
        panels = self.session.query(Device).all()
        for panel in panels:
            batch = {}
            config = json.loads(panel.panel_config)
            for key, val in config.items():
                if panel_id != 0:
                    break
                if val == op:
                    found = 1
                    pattern = r'([^_]+)_linkedProperty'
                    match = re.search(pattern, key)
                    if match:
                        found = 1
                        name = match.group(1)
                        if name == 'backlight':
                            batch["backlight"] = json.dumps({"state": value})
                        if name == 'brightness':
                            batch["backlight"] = json.dumps({"brightness": value})
                        if name == 'page':
                            batch["page"] = value
                        if name == 'idle':
                            batch["idle"] = value
                        if self.str_contains(name, "output"):
                            state = json.dumps({"state": value})
                            batch[name] = state

            for pi, page in enumerate(config["pages"]):
                for object_ in page['objects']:
                    if object_["obj"] == "template" and "template" in object_:
                        template = config["templates"][object_["template"]]
                        self.merge_objects(template[0], object_)
                        for child in template:
                            id_ = child["id"] + object_["id"]
                            for key, val in child.items():
                                str_ = self.replace_object(object_["linkedObject"], val)
                                if isinstance(str_, str) and self.str_contains(str_, op):
                                    found = 1
                                    name = f"p{pi}b{id_}.{key}"
                                    data = str_
                                    batch[name] = data
                    else:
                        for key, val in object_.items():
                            if isinstance(val, str) and self.str_contains(val, op):
                                found = 1
                                name = f"p{pi}b{object_['id']}.{key}"
                                data = val
                                batch[name] = data

            cache[panel.id] = {"MQTT": panel.mqtt_path, "batch": batch}
            if found:
                self.save_to_cache(f"hasp:{op}", json.dumps(cache))

            if batch:
                for key, val in batch.items():
                    data = self.process_value(val, op, value)
                    batch[key] = data
                if panel.id == panel_id:
                    batch.pop(name_value, None)

                self.send_batch(panel.mqtt_path, batch)

        return found
    
    def process_value(self, template, op, value):
        data = template
        if op:
            data = data.replace(op, str(value))
        
        pattern = r'%([^%\'"]+)\.([^%\'"]+)%'
        matches = re.findall(pattern, template)
        for match in matches:
            val = getProperty(match[0]+"."+match[1])
            data = data.replace("%"+match[0]+"."+match[1]+"%", str(val))
        
        if '{{' in data:
            data = self.process_title(data)
        
        return data
        match = re.search(r'{{\s*([^}]+)\s*}}', data)
        if match:
            code = match.group(1)
            try:
                # Evaluate the code and get its result
                with self._app.app_context():
                    from flask import render_template_string
                    data = render_template_string(code) # eval(code)
                self.logger.debug("Process template: %s => %s Result: %s",template, code, data)
            except ZeroDivisionError:
                self.logger.error("Error: Division by zero is not allowed (%s)",template)
                data = 'error'
            except SyntaxError:
                self.logger.error("Error: Invalid Python code (%s)", template)
                data = 'error'
            except Exception as e:
                self.logger.exception("Error: %s (%s)", e,template)
                data = 'error'
        
        return data
    
    def search(self, query: str) -> str:
        res = []
        devices = Device.query.filter(or_(Device.title.contains(query),Device.panel_config.contains(query))).all()
        for device in devices:
            res.append({"url":f'OpenHasp?op=edit&device={device.id}', "title":f'{device.title}', "tags":[{"name":"OpenHasp","color":"primary"}]})
        return res

    def check_from_cache(self, name):
        from app.extensions import cache
        return cache.get(name)     
                
    def save_to_cache(self, name, value):
        from app.extensions import cache
        return cache.set(name, value) 

    def str_contains(self, haystack, needle):
        return needle != '' and needle in haystack
    
    def process_title(self, template_string):
        try:
            env = Environment()
            template = env.from_string(template_string)
            data = {
            }
            output = template.render(data)
            self.logger.debug("Parse template: %s Result: %s", template_string, output)
            return output            
        except Exception as e:
            self.logger.exception("Error: %s (%s)", e,template_string)
            return template_string
