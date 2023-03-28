from viur.datastore.types import Entity, Key, currentTransaction, currentDbAccessLog
from viur.datastore.transport import Get, Put, RunInTransaction
from typing import Union, Tuple, List, Set, Optional
import binascii
from datetime import datetime

def fixUnindexableProperties(entry: Entity) -> Entity:
	"""
		Recursively walk the given Entity and add all properties to the list of unindexed properties if they contain
		a string longer than 500 bytes (which is maximum size of a string that can be indexed). The datastore would
		return an error otherwise.
	:param entry: The entity to fix (inplace)
	:return: The fixed entity
	"""
	def hasUnindexableProperty(prop):
		if isinstance(prop, dict):
			return any([hasUnindexableProperty(x) for x in prop.values()])
		elif isinstance(prop, list):
			return any([hasUnindexableProperty(x) for x in prop])
		elif isinstance(prop, (str, bytes)):
			return len(prop) >= 500
		else:
			return False

	resList = set()
	for k, v in entry.items():
		if hasUnindexableProperty(v):
			if isinstance(v, dict):
				innerEntry = Entity()
				innerEntry.update(v)
				entry[k] = fixUnindexableProperties(innerEntry)
				if isinstance(v, Entity):
					innerEntry.key = v.key
			else:
				resList.add(k)
	entry.exclude_from_indexes = resList
	return entry

def normalizeKey(key: Union[None, 'db.KeyClass']) -> Union[None, 'db.KeyClass']:
	"""
		Normalizes a datastore key (replacing _application with the current one)

		:param key: Key to be normalized.
		:return: Normalized key in string representation.
	"""
	if key is None:
		return None
	if key.parent:
		parent = normalizeKey(key.parent)
	else:
		parent = None
	return Key(key.kind, key.id_or_name, parent=parent)


def keyHelper(inKey: Union[Key, str, int], targetKind: str,
			  additionalAllowedKinds: Union[List[str], Tuple[str]] = (),
			  adjust_kind: bool = False) -> Key:
	if isinstance(inKey, Key):
		if inKey.kind != targetKind and inKey.kind not in additionalAllowedKinds:
			if not adjust_kind:
				raise ValueError(f"Kind mismatch: {inKey.kind!r} != {targetKind!r} (or in {additionalAllowedKinds!r})")
			inKey.kind = targetKind
		return inKey
	elif isinstance(inKey, str):
		# Try to parse key from str
		try:
			decodedKey = normalizeKey(Key.from_legacy_urlsafe(inKey))
		except:
			decodedKey = None

		# If it did decode, recall keyHelper with Key object
		if decodedKey:
			return keyHelper(
				decodedKey,
				targetKind=targetKind,
				additionalAllowedKinds=additionalAllowedKinds,
				adjust_kind=adjust_kind
			)

		# otherwise, construct key from str or int
		if inKey.isdigit():
			inKey = int(inKey)

		return Key(targetKind, inKey)
	elif isinstance(inKey, int):
		return Key(targetKind, inKey)

	raise NotImplementedError(f"Unsupported key type {type(inKey)}")

def IsInTransaction() -> bool:
	return currentTransaction.get() is not None

def GetOrInsert(key: Key, **kwargs) -> Entity:
	"""
		Either creates a new entity with the given key, or returns the existing one.

		Its guaranteed that there is no race-condition here; it will never overwrite an
		previously created entity. Extra keyword arguments passed to this function will be
		used to populate the entity if it has to be created; otherwise they are ignored.

		:param key: The key which will be fetched or created.
		:returns: Returns the fetched or newly created Entity.
	"""
	def txn(key, kwargs):
		obj = Get(key)
		if not obj:
			obj = Entity(key)
			for k, v in kwargs.items():
				obj[k] = v
			Put(obj)
		return obj

	if IsInTransaction():
		return txn(key, kwargs)
	return RunInTransaction(txn, key, kwargs)

def encodeKey(key: Key) -> str:
	"""
		Return the given key encoded as string (mimicking the old str() behaviour of keys)
	"""
	# todo: Should we make a deprecation warning when this function is used?
	return str(key)

def acquireTransactionSuccessMarker() -> str:
	"""
		Generates a token that will be written to the firestore (under "viur-transactionmarker") if the transaction
		completes successfully. Currently only used by deferredTasks to check if the task should actually execute
		or if the transaction it was created in failed.
		:return: Name of the entry in viur-transactionmarker
	"""
	txn = currentTransaction.get()
	assert txn, "acquireTransactionSuccessMarker cannot be called outside an transaction"
	marker = txn["key"] #binascii.b2a_hex(txn["key"]).decode("ASCII")
	if not "viurTxnMarkerSet" in txn:
		e = Entity(Key("viur-transactionmarker", marker))
		e["creationdate"] = datetime.utcnow()
		Put(e)
		txn["viurTxnMarkerSet"] = True
	return marker

def startDataAccessLog() -> Set[Union[Key, str]]:
	"""
		Clears our internal access log (which keeps track of which entries have been accessed in the current
		request). The old set of accessed entries is returned so that it can be restored with
		:func:`server.db.popAccessData` in case of nested caching. You must call popAccessData afterwards, otherwise
		we'll continue to log all entries accessed in subsequent request on the same thread!
		:return: Set of old accessed entries
	"""
	old = currentDbAccessLog.get(set())
	currentDbAccessLog.set(set())
	return old


def endDataAccessLog(outerAccessLog: Optional[Set[Union[Key, str]]] = None) -> Optional[Set[Union[Key, str]]]:
	"""
		Retrieves the set of entries accessed so far. To clean up and restart the log, call :func:`viur.datastore.startAccessDataLog`.
		If you called :func:`server.db.startAccessDataLog` before, you can re-apply the old log using
		the outerAccessLog param. Otherwise, it will disable the access log.
		:param outerAccessLog: State of your log returned by :func:`server.db.startAccessDataLog`
		:return: Set of entries accessed
	"""
	res = currentDbAccessLog.get()
	if isinstance(outerAccessLog, set):
		currentDbAccessLog.set((outerAccessLog or set()).union(res))
	else:
		currentDbAccessLog.set(None)
	return res
