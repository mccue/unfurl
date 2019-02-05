from collections import Mapping, MutableSequence, MutableMapping
from .util import diffDicts

def serializeValue(value, **kw):
  getter = getattr(value, 'asRef', None)
  if getter:
    return getter(kw)
  if isinstance(value, Mapping):
    return dict((key, serializeValue(v, **kw)) for key, v in value.items())
  elif isinstance(value, (MutableSequence, tuple)):
    return [serializeValue(item, **kw) for item in value]
  else:
    return value

class ResourceRef(object):
  # ABC requires 'parent', and '_resolve'

  def _getProp(self, name):
    if name == '.':
      return self
    elif name == '..':
      return self.parent
    name = name[1:]
    # XXX3 use propmap
    return getattr(self, name)

  def __reflookup__(self, key):
    if not key:
      raise KeyError(key)
    if key[0] == '.':
      return self._getProp(key)

    return self._resolve(key)

  def yieldParents(self):
    "yield self and ancestors starting from self"
    resource = self
    while resource:
      yield resource
      resource = resource.parent

  @property
  def ancestors(self):
    return list(self.yieldParents())

  @property
  def parents(self):
    """list of parents starting from root"""
    return list(reversed(self.ancestors))[:-1]

  @property
  def root(self):
    return self.ancestors[-1]

  @property
  def all(self):
    return self.root._all

  @property
  def templar(self):
    return self.root._templar

class ChangeAware(object):
  def hasChanged(self, changeset):
    return False

class ExternalValue(ChangeAware):

  def get(self):
    pass

  def __eq__(self, other):
    if isinstance(other, ExternalValue):
      return self.keys == other.keys and self.get() == other.get()
    return self.resolve() == other

#XXX
  def _setResolved(self, key, value):
    self.getter = key

# XXX __setstate__

  def resolve(self, key=None):
    key = key or self.getter
    if key:
      value = self.get()
      getter = getattr(value, '__reflookup__', None)
      if getter:
        return getter(key)
      else:
        return value[key]
    else:
      return self.get()

  def asRef(self, options=None):
    if options and options.get('resolveExternal'):
      return serializeValue(self.resolve(), **options)
    # external:local external:secret
    serialized = {self.type: self.key}
    if self.getter:
      serialized['get'] = self.getter
    return {'ref': serialized}

_Deleted = object()
class Result(ChangeAware):
  __slots__ = ('original', 'resolved', 'external')

  def __init__(self, resolved, original = _Deleted):
    self.original = original
    if isinstance(resolved, ExternalValue):
      self.resolved = resolved.resolve()
      assert not isinstance(self.resolved, Result), self.resolved
      self.external = resolved
    else:
      assert not isinstance(resolved, Result), resolved
      self.resolved = resolved
      self.external = None

  def asRef(self, options=None):
    if self.external:
      return self.external.asRef()
    else:
      val = serializeValue(self.resolved, **(options or {}))
      return val

  def hasDiff(self):
    if self.original is _Deleted: # this is a new item
      return True
    else:
      if isinstance(self.resolved, Results):
        return self.resolved.hasDiff()
      else:
        newval = self.asRef()
        if self.original != newval:
          return True
    return False

  def getDiff(self):
    if isinstance(self.resolved, Results):
      return self.resolved.getDiff()
    else:
      val = self.asRef()
      if isinstance(val, Mapping):
        old = serializeValue(self.original)
        if isinstance(old, Mapping):
          return diffDicts(old, val)
      return val

  def _values(self):
    resolved = self.resolved
    if isinstance(resolved, ResultsMap):
      return (resolved._attributes[k] for (k, v) in resolved.items())
    elif isinstance(resolved, Mapping):
      return resolved.values()
    else:
      return None

  def resolveKey(self, key):
    if self.external:
      value = self.external.resolve(key)
    else:
      getter = getattr(self.resolved, '__reflookup__', None)
      if getter:
        value = getter(key)
      else:
        value = self.resolved[key]
    return value

  def hasChanged(self, changeset):
    if self.external:
      return self.external.hasChanged(changeset)
    elif isinstance(self.resolved, ChangeAware):
      return self.resolved.hasChanged(changeset)
    else:
      return False

  def __eq__(self, other):
    if isinstance(other, Result):
      return self.resolved == other.resolved
    else:
      return self.resolved == other

  def __repr__(self):
    return "Result(%r)" % self.resolved

class Results(object):
  """
  Evaluating expressions are not guaranteed to be idempotent (consider quoting)
  and resolving the whole tree up front can lead to evaluations of cicular references unless the
  order is carefully chosen. So evaluate lazily and memoize the results.
  """

  __slots__ = ('_attributes', 'context', '_deleted')

  def __init__(self, serializedOriginal, resourceOrCxt):
      from .eval import RefContext
      assert not isinstance(serializedOriginal, Results), serializedOriginal
      self._attributes = serializedOriginal
      self._deleted = {}
      if not isinstance(resourceOrCxt, RefContext):
        resourceOrCxt = RefContext(resourceOrCxt)
      self.context = resourceOrCxt

  def hasDiff(self):
    return any(isinstance(x, Result) and x.hasDiff() for x in self._attribute)

  def _serializeItem(self, val):
    if isinstance(val, Result):
      return val.asRef()
    else: # never resolved, so already in serialized form
      return val

  @staticmethod
  def _mapValue(val, context):
    "Recursively and lazily resolves any references in a value"
    # XXX but should return ExternalValues sometimes?!
    from .eval import mapValue, Ref
    if isinstance(val, Result):
      return val.resolved
    elif isinstance(val, Results):
      return val
    elif Ref.isRef(val):
      result = Ref(val).resolveOne(context)
      return result
    elif isinstance(val, Mapping):
      return ResultsMap(val, context)
    elif isinstance(val, (list,tuple)):
      return ResultsList(val, context)
    else:
      # at this point, just evaluates templates in strings
      return mapValue(val, context)

  def __getitem__(self, key):
    val = self._attributes[key]
    if isinstance(val, Result):
      assert not isinstance(val.resolved, Result), val
      return val.resolved
    else:
      resolved = self._mapValue(val, self.context)
      self._attributes[key] = Result(resolved, val)
      assert not isinstance(resolved, Result), val
      return resolved

  def __setitem__(self, key, value):
    assert not isinstance(value, Result), (key, value)
    self._attributes[key] = Result(value)
    self._deleted.pop(key, None)

  def __delitem__(self, index):
    val = self._attributes[index]
    self._deleted[index] = val
    del self._attributes[index]

  def __len__(self):
    return len(self._attributes)

  def __eq__(self, other):
    if isinstance(other, Results):
      return self._attributes == other._attributes
    else:
      self.resolveAll()
      return self._attributes == other

  def __repr__(self):
    return "Results(%r)" % self._attributes


class ResultsMap(Results, MutableMapping):

  def __iter__(self):
      return iter(self._attributes)

  def asRef(self, options=None):
    return dict((key, self._serializeItem(val)) for key, val in self._attributes.items())

  def resolveAll(self):
    list(self.values())

  def getDiff(self, cls=dict):
    # returns a dict with the same semantics as diffDicts
    diffDict = cls()
    for key, val in self._attributes.items():
      if isinstance(val, Result) and val.hasDiff():
        diffDict[key] = val.getDiff()

    for key in self._deleted:
      diffDict[key]= {'+%': 'delete'}

    return diffDict


class ResultsList(Results, MutableSequence):

  def insert(self, index, value):
    assert not isinstance(value, Result), value
    self._attributes.insert(index, Result(value))

  def asRef(self, options=None):
    return [self._serializeItem(val) for val in self._attributes]

  def getDiff(self, cls=list):
    # we don't have patchList yet so just returns the whole list
    return cls(val.getDiff() if isinstance(val, Result) else val for val in self._attributes)

  def resolveAll(self):
    list(self)
