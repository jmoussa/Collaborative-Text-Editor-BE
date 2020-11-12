import logging
import pymongo
import pydantic
import json

from pydantic import BaseModel

from fastapi import FastAPI, WebSocket, Depends, BackgroundTasks

from starlette.websockets import WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.status import HTTP_201_CREATED, HTTP_400_BAD_REQUEST

from config import MONGODB_DB_NAME
from mongodb import close_mongo_connection, connect_to_mongo, get_nosql_db, AsyncIOMotorClient
from controllers import (
    create_user,
    get_user,
    verify_password,
    create_access_token,
)
from models import UserInResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # can alter with time
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Notifier:
    """
        Manages chat room sessions and members along with message routing
    """

    def __init__(self):
        self.generator = self.get_notification_generator()
        self.websocket = None

    def set_websocket(self, websocket: WebSocket):
        self.websocket = websocket

    async def get_notification_generator(self):
        while True:
            message = yield
            msg = message["editorState"]
            username = message["username"]
            await self._notify(msg, username)

    async def push(self, msg: str, username: str = None):
        message_body = {"editorState": msg, "username": username}
        await self.generator.asend(message_body)

    async def connect(self, websocket: WebSocket, room_name: str):
        await websocket.accept()

    def remove(self, websocket: WebSocket, username: str):
        print(f"{username} left.")

    async def _notify(self, message: str, username: str):
        websocket = self.websocket
        if websocket is not None:
            await websocket.send_text(str({"editorState": message, "username": username}))


notifier = Notifier()


@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    client = await get_nosql_db()
    db = client[MONGODB_DB_NAME]
    try:
        # await notifier.generator.asend("Hello There. ^_^")

        await db.create_collection("users")
        user_collection = db.users
        await user_collection.create_index("username", name="username", unique=True)
    except pymongo.errors.CollectionInvalid as e:
        logging.info(e)
        pass


@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.put("/register", tags=["authentication"], status_code=HTTP_201_CREATED)
async def register_user(request: RegisterRequest, client: AsyncIOMotorClient = Depends(get_nosql_db)):
    try:
        db = client[MONGODB_DB_NAME]
        collection = db.users
        dbuser = await create_user(request, collection)
        token = create_access_token(data={"username": dbuser["username"]})
        print(f"REGISTER: {token}")
        return UserInResponse(**dbuser, token=token)
        # return get_main(request, token, dbuser)
    except pydantic.error_wrappers.ValidationError as e:
        return e
    except pymongo.errors.DuplicateKeyError as e:
        return {"error": "username already exists", "verbose": e}


@app.put("/login", tags=["authentication"])
async def login_user(request: RegisterRequest, client: AsyncIOMotorClient = Depends(get_nosql_db)):
    db = client[MONGODB_DB_NAME]
    collection = db.users
    dbuser = await get_user(request.username, collection=collection)
    if not dbuser or not verify_password((request.password + dbuser["salt"]), dbuser["hashed_password"]):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Incorrect email or password")
    else:
        token = create_access_token(data={"username": dbuser["username"]})
        print(f"LOGIN: {token}")
        return UserInResponse(**dbuser, token=token)
        # return get_main(request, token, dbuser)


@app.websocket("/ws/{room_name}")
async def websocket_endpoint(websocket: WebSocket, room_name, background_tasks: BackgroundTasks):
    notifier.set_websocket(websocket)
    await notifier.connect(websocket, room_name)
    await notifier.generator.asend("Hello There. ^_^")
    try:
        while True:
            data = await websocket.receive_text()
            state = json.loads(data)
            await notifier.push(f"{state.editorState}", state.username)
    except WebSocketDisconnect:
        notifier.remove(websocket, data.username)
