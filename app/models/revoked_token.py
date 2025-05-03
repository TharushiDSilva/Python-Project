from app import db

class RevokedToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(120), unique=True, nullable=False)

    def __init__(self, jti):
        self.jti = jti

    def save_to_db(self):
        db.session.add(self)
        db.session.commit()

    @classmethod
    def is_jti_blacklisted(cls, jti):
        return cls.query.filter_by(jti=jti).first() is not None
