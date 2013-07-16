
from webob.dec import wsgify
from webob.exc import *
from webob import Response

try:
    import simplejson as json
    from simplejson import JSONDecodeError
except ImportError:
    import json
    JSONDecodeError = ValueError

import os, logging

__all__ = ['RestApp', 'RestHandlerBase', 'hasReturnValue', 'noReturnValue']

def noReturnValue(func):
    func.hasReturnValue = False
    return func

class RestHandlerBase(object):
    """Parent class for a handler group."""
    hasReturnValue = True

class _RestHandlerWrapper(RestHandlerBase):
    """Wrapper for functions that we can't modify."""
    def __init__(self, func_name, func, hasReturnValue=True):
        self.func_name = func_name
        self.func = func
        self.hasReturnValue = hasReturnValue
    def __call__(self, *args, **kw):
        return self.func(*args, **kw)

class RestApp(object):
    """A WSGI app that implements RESTful operations on Collections, Decks and Cards."""

    # Defines not only the valid handler types, but their position in the URL string
    # TODO: this broken - it allows a model to contain cards, for example.. We need to
    #       give a pattern for each handler type.
    handler_types = ['collection', ['model', 'note', 'deck'], 'card']

    def __init__(self, data_root, allowed_hosts='*', use_default_handlers=True, collection_manager=None):
        from AnkiServer.threading import getCollectionManager

        self.data_root = os.path.abspath(data_root)
        self.allowed_hosts = allowed_hosts

        if collection_manager is not None:
            col = collection_manager
        else:
            col = getCollectionManager()

        self.handlers = {}
        for type_list in self.handler_types:
            if type(type_list) is not list:
                type_list = [type_list]
            for handler_type in type_list:
                self.handlers[handler_type] = {}

        if use_default_handlers:
            self.add_handler_group('collection', CollectionHandler())
            self.add_handler_group('note', NoteHandler())
            self.add_handler_group('model', ModelHandler())
            self.add_handler_group('deck', DeckHandler())
            self.add_handler_group('card', CardHandler())

    def add_handler(self, type, name, handler):
        """Adds a callback handler for a type (collection, deck, card) with a unique name.
        
         - 'type' is the item that will be worked on, for example: collection, deck, and card.

         - 'name' is a unique name for the handler that gets used in the URL.

         - 'handler' is a callable that takes (collection, data, ids).
        """

        if self.handlers[type].has_key(name):
            raise "Handler already for %(type)s/%(name)s exists!"
        self.handlers[type][name] = handler

    def add_handler_group(self, type, group):
        """Adds several handlers for every public method on an object descended from RestHandlerBase.
        
        This allows you to create a single class with several methods, so that you can quickly
        create a group of related handlers."""

        import inspect
        for name, method in inspect.getmembers(group, predicate=inspect.ismethod):
            if not name.startswith('_'):
                if hasattr(group, 'hasReturnValue') and not hasattr(method, 'hasReturnValue'):
                    method = _RestHandlerWrapper(group.__class__.__name__ + '.' + name, method, group.hasReturnValue)
                self.add_handler(type, name, method)

    def _checkRequest(self, req):
        """Raises an exception if the request isn't allowed or valid for some reason."""
        if self.allowed_hosts != '*':
            try:
                remote_addr = req.headers['X-Forwarded-For']
            except KeyError:
                remote_addr = req.remote_addr
            if remote_addr != self.allowed_hosts:
                raise HTTPForbidden()
        
        if req.method != 'POST':
            raise HTTPMethodNotAllowed(allow=['POST'])

    def _parsePath(self, path):
        """Takes a request path and returns a tuple containing the handler type, name
        and a list of ids.

        Raises an HTTPNotFound exception if the path is invalid."""

        if path in ('', '/'):
            raise HTTPNotFound()

        # split the URL into a list of parts
        if path[0] == '/':
            path = path[1:]
        parts = path.split('/')

        # pull the type and context from the URL parts
        handler_type = None
        ids = []
        for type_list in self.handler_types:
            if len(parts) == 0:
                break

            # some URL positions can have multiple types
            if type(type_list) is not list:
                type_list = [type_list]

            # get the handler_type
            if parts[0] not in type_list:
                break
            handler_type = parts.pop(0)

            # add the id to the id list
            if len(parts) > 0:
                ids.append(parts.pop(0))
            # break if we don't have enough parts to make a new type/id pair
            if len(parts) < 2:
                break

        # sanity check to make sure the URL is valid
        if len(parts) > 1 or len(ids) == 0:
            raise HTTPNotFound()

        # get the handler name
        if len(parts) == 0:
            name = 'index'
        else:
            name = parts[0]

        return (handler_type, name, ids)

    def _getCollectionPath(self, collection_id):
        """Returns the path to the collection based on the collection_id from the request.
        
        Raises HTTPBadRequest if the collection_id is invalid."""

        path = os.path.normpath(os.path.join(self.data_root, collection_id, 'collection.anki2'))
        if path[0:len(self.data_root)] != self.data_root:
            # attempting to escape our data jail!
            raise HTTPBadRequest('"%s" is not a valid collection' % collection_id)

        return path

    def _getHandler(self, type, name):
        """Returns a tuple containing handler function for this type and name, and a boolean flag
        if that handler has a return value.

        Raises an HTTPNotFound exception if the handler doesn't exist."""

        # get the handler function
        try:
            handler = self.handlers[type][name]
        except KeyError:
            raise HTTPNotFound()
         
        # get if we have a return value
        hasReturnValue = True
        if hasattr(handler, 'hasReturnValue'):
            hasReturnValue = handler.hasReturnValue

        return (handler, hasReturnValue)

    def _parseRequestBody(self, req):
        """Parses the request body (JSON) into a Python dict and returns it.

        Raises an HTTPBadRequest exception if the request isn't valid JSON."""
        
        try:
            data = json.loads(req.body)
        except JSONDecodeError, e:
            logging.error(req.path+': Unable to parse JSON: '+str(e), exc_info=True)
            raise HTTPBadRequest()

        # make the keys into non-unicode strings
        data = dict([(str(k), v) for k, v in data.items()])

        return data

    @wsgify
    def __call__(self, req):
        # make sure the request is valid
        self._checkRequest(req)

        # parse the path
        type, name, ids = self._parsePath(req.path)

        # get the collection path
        collection_path = self._getCollectionPath(ids[0])
        print collection_path

        # get the handler function
        handler, hasReturnValue = self._getHandler(type, name)

        # parse the request body
        data = self._parseRequestBody(req)

        # debug
        from pprint import pprint
        pprint(data)

        # run it!
        col = self.collection_manager.get_collection(collection_path)
        try:
            output = col.execute(handler, [data, ids], {}, hasReturnValue)
        except Exception, e:
            logging.error(e)
            return HTTPInternalServerError()

        if output is None:
            return Response('', content_type='text/plain')
        else:
            return Response(json.dumps(output), content_type='application/json')

class CollectionHandler(RestHandlerBase):
    """Default handler group for 'collection' type."""
    
    #
    # MODELS - Store fields definitions and templates for notes
    #

    def list_models(self, col, data, ids):
        # This is already a list of dicts, so it doesn't need to be serialized
        return col.models.all()

    def find_model_by_name(self, col, data, ids):
        # This is already a list of dicts, so it doesn't need to be serialized
        return col.models.byName(data['model'])

    #
    # NOTES - Information (in fields per the model) that can generate a card
    #         (based on a template from the model).
    #

    def find_notes(self, col, data, ids):
        query = data.get('query', '')
        ids = col.findNotes(query)

        if data.get('preload', False):
            nodes = [NoteHandler._serialize(col.getNote(id)) for id in ids]
        else:
            nodes = [{'id': id} for id in ids]

        return nodes

    @noReturnValue
    def add_note(self, col, data, ids):
        from anki.notes import Note

        # TODO: I think this would be better with 'model' for the name
        # and 'mid' for the model id.
        if type(data['model']) in (str, unicode):
            model = col.models.byName(data['model'])
        else:
            model = col.models.get(data['model'])

        note = Note(col, model)
        for name, value in data['fields'].items():
            note[name] = value

        if data.has_key('tags'):
            note.setTagsFromStr(data['tags'])

        col.addNote(note)

    #
    # DECKS - Groups of cards
    #

    def list_decks(self, col, data, ids):
        # This is already a list of dicts, so it doesn't need to be serialized
        return col.decks.all()

    @noReturnValue
    def select_deck(self, col, data, ids):
        col.decks.select(data['deck_id'])

    #
    # CARD - A specific card in a deck with a history of review (generated from
    #        a note based on the template).
    #

    def find_cards(self, col, data, ids):
        query = data.get('query', '')
        ids = col.findCards(query)

        if data.get('preload', False):
            cards = [CardHandler._serialize(col.getCard(id)) for id in ids]
        else:
            cards = [{'id': id} for id in ids]

        return cards

    #
    # SCHEDULER - Controls card review, ie. intervals, what cards are due, answering a card, etc.
    #

    @noReturnValue
    def sched_reset(self, col, data, ids):
        col.sched.reset()


class ImportExportHandler(RestHandlerBase):
    """Handler group for the 'collection' type, but it's not added by default."""

    def _get_filedata(self, data):
        import urllib2

        if data.has_key('data'):
            return data['data']

        fd = None
        try:
            fd = urllib2.urlopen(data['url'])
            filedata = fd.read()
        finally:
            if fd is not None:
                fd.close()

        return filedata

    def _get_importer_class(self, data):
        filetype = data['filetype']

        # We do this as an if/elif/else guy, because I don't want to even import
        # the modules until someone actually attempts to import the type
        if filetype == 'text':
            from anki.importing.csvfile import TextImporter
            return TextImporter
        elif filetype == 'apkg':
            from anki.importing.apkg import AnkiPackageImporter
            return AnkiPackageImporter
        elif filetype == 'anki1':
            from anki.importing.anki1 import Anki1Importer
            return Anki1Importer
        elif filetype == 'supermemo_xml':
            from anki.importing.supermemo_xml import SupermemoXmlImporter
            return SupermemoXmlImporter
        elif filetype == 'mnemosyne':
            from anki.importing.mnemo import MnemosyneImporter
            return MnemosyneImporter
        elif filetype == 'pauker':
            from anki.importing.pauker import PaukerImporter
            return PaukerImporter
        else:
            raise HTTPBadRequest("Unknown filetype '%s'" % filetype)

    def import_file(self, col, data, ids):
        import tempfile

        # get the importer class
        importer_class = self._get_importer_class(data)

        # get the file data
        filedata = self._get_filedata(data)

        # write the file data to a temporary file
        try:
            path = None
            with tempfile.NamedTemporaryFile('wt', delete=False) as fd:
                path = fd.name
                fd.write(filedata)

            importer = importer_class(col, path)
            importer.open()
            importer.run()
        finally:
            if path is not None:
                os.unlink(path)

class ModelHandler(RestHandlerBase):
    """Default handler group for 'model' type."""

    def field_names(self, col, data, ids):
        model = col.models.get(ids[1])
        if model is None:
            raise HTTPNotFound()
        return col.models.fieldNames(model)

class NoteHandler(RestHandlerBase):
    """Default handler group for 'note' type."""

    @staticmethod
    def _serialize(note):
        d = {
            'id': note.id,
            'model': note.model()['name'],
            'tags': ' '.join(note.tags),
        }
        # TODO: do more stuff!
        return d

    def index(self, col, data, ids):
        note = col.getNote(ids[1])
        return self._serialize(note)

class DeckHandler(RestHandlerBase):
    """Default handler group for 'deck' type."""

    def next_card(self, col, data, ids):
        deck_id = ids[1]

        col.decks.select(deck_id)
        card = col.sched.getCard()
        if card is None:
            return None

        return CardHandler._serialize(card)

class CardHandler(RestHandlerBase):
    """Default handler group for 'card' type."""

    @staticmethod
    def _serialize(card):
        d = {
            'id': card.id
        }
        # TODO: do more stuff!
        return d

# Our entry point
def make_app(global_conf, **local_conf):
    # setup the logger
    logging_config_file = local_conf.get('logging.config_file')
    if logging_config_file:
        # monkey patch the logging.config.SMTPHandler if necessary
        import sys
        if sys.version_info[0] == 2 and sys.version_info[1] == 5:
            import AnkiServer.logpatch

        # load the config file
        import logging.config
        logging.config.fileConfig(logging_config_file)

    return RestApp(
        data_root=local_conf.get('data_root', '.'),
        allowed_hosts=local_conf.get('allowed_hosts', '*')
    )

