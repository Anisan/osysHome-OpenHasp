import json, re
from flask import redirect, render_template
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired

from app.database import db
from app.extensions import cache
from plugins.OpenHasp.models.Device import Device
from app.core.lib.object import setLinkToObject, removeLinkFromObject

class DeviceForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired()])
    mqtt_path = StringField('MQTT path', validators=[DataRequired()])
    panel_config = TextAreaField('Config (JSON)')

def unset_linked(config):
    find_linked(config, False)

def set_linked(config):
    find_linked(config, True)
    # clear cache data hasp
    for k in list(cache.cache._cache):
        if "hasp:" in k:
           cache.delete(k)

def find_linked(config, add):
    if config == '':
        return
    pattern = r'%([^%\'"]+)\.([^%\'"]+)%'
    matches = re.findall(pattern, config)

    for match in matches:
        if add:
            setLinkToObject(match[0], match[1], "OpenHasp")
        else:
            removeLinkFromObject(match[0], match[1], "OpenHasp")

        # link for templates
    config = json.loads(config)
    if 'pages' not in config:
        return
    for page in config['pages']:
        for obj in page['objects']:
            if obj["obj"] == 'template' and "linkedObject" in obj:
                template = config['templates'][obj['template']]
                template_str = json.dumps(template)
                template_matches = re.findall(r'%\.([^%\'"]+)%', template_str)
                for tmpl_match in template_matches:
                    if add:
                        setLinkToObject(obj["linkedObject"], tmpl_match, "OpenHasp")
                    else:
                        removeLinkFromObject(obj["linkedObject"], tmpl_match, "OpenHasp")
    
def routeDevice(request):
    id = request.args.get('device', None)
    op = request.args.get('op', '')
    if id:
        item = Device.query.get_or_404(id)  # Получаем объект из базы данных или возвращаем 404, если не найден
        form = DeviceForm(obj=item)  # Передаем объект в форму для редактирования
    else:
        form = DeviceForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            if id:
                unset_linked(item.panel_config)
                form.populate_obj(item)  # Обновляем значения объекта данными из формы
            else:
                item = Device()
                form.populate_obj(item)
                db.session.add(item)
            set_linked(item.panel_config)
            db.session.commit()  # Сохраняем изменения в базе данных
            return redirect("OpenHasp")
        
    return render_template('openhasp_device.html', id=id, form=form)
