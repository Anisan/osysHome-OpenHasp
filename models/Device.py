
from app.database import Column, Model, SurrogatePK, db

class HaspDevice(SurrogatePK, db.Model):
    __tablename__ = 'openehasp_device'
    title = Column(db.String(100))
    mqtt_path = Column(db.String(255))
    current_page = Column(db.Integer)
    online = Column(db.Boolean)
    ip = Column(db.String(50))
    panel_config = Column(db.Text)