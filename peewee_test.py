from peewee import *
db = MySQLDatabase('bot_puzzles', host='localhost', port=3306, user='bot', password='hIndradKibogYNanTErC')


class MyUser (Model):
    name = TextField()
    city = TextField(constraints=[SQL("DEFAULT 'Mumbai'")])
    age = IntegerField()

    class Meta:
        database = db
        db_table = 'MyUser2'


db.connect()
db.create_tables([MyUser])
