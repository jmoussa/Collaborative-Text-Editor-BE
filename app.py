import logging
import pymongo
import pydantic
import json

from pydantic import BaseModel

from fastapi import FastAPI, WebSocket, Depends, BackgroundTasks

from diff_match_patch import diff_match_patch

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
    get_or_create_document_from_server,
    update_server_text,
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
            await self._notify(str(message))

    async def push(self, msg: str):
        websocket = self.websocket
        if msg is not None and websocket is not None:
            logging.info(f"PUSHING: ({msg})")
            await websocket.send_text(msg)

    async def connect(self, websocket: WebSocket, room_name: str):
        await websocket.accept()

    async def _notify(self, message: str):
        websocket = self.websocket
        if message is not None and websocket is not None:
            logging.info(f"NOTIFY: {message}")
            await self.generator.asend(message)
            # await websocket.send_text(message)


notifier = Notifier()


@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    client = await get_nosql_db()
    db = client[MONGODB_DB_NAME]
    try:
        await db.create_collection("documents")
        document_collection = db.documents
        await document_collection.create_index("doc_id", name="doc_id", unique=True)

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
    dbuser = await get_user(request["username"], collection=collection)
    if not dbuser or not verify_password((request.password + dbuser["salt"]), dbuser["hashed_password"]):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Incorrect email or password")
    else:
        token = create_access_token(data={"username": dbuser["username"]})
        print(f"LOGIN: {token}")
        return UserInResponse(**dbuser, token=token)


@app.get("/document/{room_name}")
async def get_initial_server_content(room_name, Background_tasks: BackgroundTasks):
    server = await get_or_create_document_from_server(room_name)
    return server    

@app.websocket("/ws/{room_name}")
async def websocket_endpoint(websocket: WebSocket, room_name, background_tasks: BackgroundTasks):
    notifier.set_websocket(websocket)
    await notifier.connect(websocket, room_name)
    doc_id = room_name
    server = await get_or_create_document_from_server(doc_id)
    logging.info(f"SENDING INITIAL SERVER STATE: {server}")
    await notifier.push(str(server))

    try:
        while True:
            str_data = await websocket.receive_text()
            logging.info(f"RECIEVED: {str_data}")
            dict_data = json.loads(str_data)
            # PERFORM DIFF_MATCH_PATCH HERE AND RETURN PATCHED VERSION
            dmp = diff_match_patch()
            server = await get_or_create_document_from_server(doc_id)
            if server is not None:
                patches = dmp.patch_make(server, dict_data["editorState"])
                new_text, _ = dmp.patch_apply(patches, server)
                server = new_text
                await update_server_text(new_text, doc_id)
                await notifier.push(str(server))
            else:
                logging.error("SERVER returned None")
    except WebSocketDisconnect:
        print("Websocket Disconnected")
