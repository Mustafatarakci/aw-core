import logging
from typing import List, Dict
from datetime import datetime, timezone

from aw_core.models import Event

# MongoDB
try:
    import pymongo
except ImportError:  # pragma: no cover
    logging.warning("Could not import pymongo, not available as a datastore backend")

from . import logger, AbstractStorage


class MongoDBStorage(AbstractStorage):
    """Uses a MongoDB server as backend"""

    def __init__(self, testing) -> None:
        self.logger = logger.getChild("mongodb")

        self.client = pymongo.MongoClient(serverSelectionTimeoutMS=5000)
        # Try to connect to the server to make sure that it's available
        # If it isn't, it will raise pymongo.errors.ServerSelectionTimeoutError
        self.client.server_info()

        self.db = self.client["activitywatch" + ("-testing" if testing else "")]

    def create_bucket(self, bucket_id: str, type_id: str, client: str, hostname: str, created: str, name: str = None) -> None:
        if not name:
            name = bucket_id
        metadata = {
            "_id": "metadata",
            "id": bucket_id,
            "name": name,
            "type": type_id,
            "client": client,
            "hostname": hostname,
            "created": created,
        }
        self.db[bucket_id]["metadata"].insert_one(metadata)

    def delete_bucket(self, bucket_id: str) -> None:
        self.db[bucket_id]["events"].drop()
        self.db[bucket_id]["metadata"].drop()

    def buckets(self) -> Dict[str, dict]:
        bucketnames = set()
        for bucket_coll in self.db.collection_names():
            bucketnames.add(bucket_coll.split('.')[0])
        buckets = dict()
        for bucket_id in bucketnames:
            buckets[bucket_id] = self.get_metadata(bucket_id)
        return buckets

    def get_metadata(self, bucket_id: str) -> dict:
        metadata = self.db[bucket_id]["metadata"].find_one({"_id": "metadata"})
        if metadata:
            del metadata["_id"]
        return metadata

    def get_events(self, bucket_id: str, limit: int,
                   starttime: datetime=None, endtime: datetime=None):
        query_filter = {}  # type: Dict[str, dict]
        if starttime:
            query_filter["timestamp"] = {}
            query_filter["timestamp"]["$gt"] = starttime
        if endtime:
            if "timestamp" not in query_filter:
                query_filter["timestamp"] = {}
            query_filter["timestamp"]["$lt"] = endtime
        if limit <= 0:
            limit = 10**9

        ds_events = list(self.db[bucket_id]["events"].find(query_filter).sort([("timestamp", -1)]).limit(limit))
        events = []
        for event in ds_events:
            event.pop('_id')
            event["timestamp"] = [ts.replace(tzinfo=timezone.utc) for ts in event["timestamp"]]
            events.append(Event(**event))
        return events

    def _transform_event(self, event: dict) -> dict:
        if "duration" in event:
            event["duration"] = [{"value": td.total_seconds(), "unit": "s"} for td in event["duration"]]
        return event

    def insert_one(self, bucket: str, event: Event):
        # .copy is needed because otherwise mongodb inserts a _id field into the event
        dict_event = self._transform_event(event.copy())
        self.db[bucket]["events"].insert_one(dict_event)

    def insert_many(self, bucket: str, events: List[Event]):
        # .copy is needed because otherwise mongodb inserts a _id field into the event
        dict_events = [self._transform_event(event.copy()) for event in events]  # type: List[dict]
        self.db[bucket]["events"].insert_many(dict_events)

    def replace_last(self, bucket_id: str, event: Event):
        last_event = list(self.db[bucket_id]["events"].find().sort([("timestamp", -1)]).limit(1))[0]
        self.db[bucket_id]["events"].replace_one({"_id": last_event["_id"]}, event.to_json_dict())

