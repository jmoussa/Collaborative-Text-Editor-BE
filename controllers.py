import hashlib
import uuid
from models import User, UserInDB, DocumentInDB, TokenPayload
import jwt

# from bson import ObjectId
from mongodb import get_nosql_db, AsyncIOMotorClient
from config import MONGODB_DB_NAME, SECRET_KEY, JWT_TOKEN_PREFIX, ACCESS_TOKEN_EXPIRE_MINUTES
from datetime import datetime, timedelta

from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND
from starlette.exceptions import HTTPException
from jwt import PyJWTError

from fastapi import Depends, Header


async def create_user(request, collection):
    salt = uuid.uuid4().hex
    hashed_password = hashlib.sha512(request.password.encode("utf-8") + salt.encode("utf-8")).hexdigest()

    user = {}
    user["username"] = request.username
    user["salt"] = salt
    user["hashed_password"] = hashed_password
    dbuser = UserInDB(**user).dict()
    row = await collection.insert_one(dbuser)
    dbuser["id"] = row.inserted_id
    return dbuser


async def get_user(name, collection=None) -> UserInDB:
    if collection is None:
        client = await get_nosql_db()
        db = client[MONGODB_DB_NAME]
        collection = db.users

    row = await collection.find_one({"username": name})
    if row is not None:
        return UserInDB(**row).dict()
    else:
        return None


def verify_password(plain_password_w_salt, hashed_password):
    checked_password = hashlib.sha512(plain_password_w_salt.encode("utf-8")).hexdigest()
    return checked_password == hashed_password


def write_notification(message):
    with open("messages.log", mode="a+") as _file:
        content = f"{datetime.utcnow()}: {message}\n"
        _file.write(content)


def create_access_token(*, data: dict):
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire, "sub": "access"})
    encoded_jwt = jwt.encode(to_encode, str(SECRET_KEY), algorithm="HS256")
    return encoded_jwt


def _get_auth_token(authorization: str = Header(...)):
    token_prefix, token = authorization.split(" ")
    if token_prefix != JWT_TOKEN_PREFIX:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid authorization type")
    return token


async def _get_current_user(
    db: AsyncIOMotorClient = Depends(get_nosql_db), token: str = Depends(_get_auth_token)
) -> User:
    try:
        payload = jwt.decode(token, str(SECRET_KEY), algorithms=["HS256"])
        token_data = TokenPayload(**payload)
    except PyJWTError:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Could not validate credentials")

    dbuser = await get_user(token_data.username)
    if not dbuser:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="User not found")

    user = User(**dbuser.dict(), token=token)
    return user


async def get_document_by_doc_id(doc_id):
    client = await get_nosql_db()
    db = client[MONGODB_DB_NAME]
    collection = db.documents
    row = await collection.find_one({"doc_id": doc_id})
    if row is not None:
        return DocumentInDB(**row).dict()
    else:
        return None


async def update_server_text(new_text, doc_id):
    client = await get_nosql_db()
    db = client[MONGODB_DB_NAME]
    collection = db.documents
    original_record = await get_document_by_doc_id(doc_id)
    if original_record is not None:
        old_text = original_record["text"]
        if old_text != new_text:
            row = await collection.update_one({"doc_id": doc_id}, {"$set": {"text": new_text}})
            return True if row is not None else False
        else:
            return True
    else:
        return False


async def get_or_create_document_from_server(document_id):
    client = await get_nosql_db()
    db = client[MONGODB_DB_NAME]
    collection = db.documents

    row = await collection.find_one({"doc_id": document_id})
    if row is not None:
        return DocumentInDB(**row).dict()["text"]
    else:
        # create empty document
        new_body = {}
        new_body["text"] = ""
        new_body["doc_id"] = document_id
        dbdoc = DocumentInDB(**new_body).dict()
        row = await collection.insert_one(dbdoc)
        return dbdoc["text"] if row["acknowledged"] else None
