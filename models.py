from pydantic import BaseModel, Field
from datetime import datetime
from bson import ObjectId


# nosql/mongodb
class User(BaseModel):
    username: str


class UserInDB(User):
    hashed_password: str
    salt: str
    date_created: datetime = Field(default_factory=datetime.utcnow)


class UserInResponse(User):
    token: str


class Message(BaseModel):
    user: UserInDB
    content: str = None


class MessageInDB(Message):
    _id: ObjectId
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TokenPayload(BaseModel):
    username: str = ""
