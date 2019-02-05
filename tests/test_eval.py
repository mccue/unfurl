import unittest
import os
from giterop.eval import Ref, mapValue, runTemplate, RefContext
from giterop.runtime import Resource
from giterop.result import ResultsList, serializeValue
from ruamel.yaml.comments import CommentedMap

class EvalTest(unittest.TestCase):
  longMessage = True

  def test_CommentedMap(self):
    cm = CommentedMap()
    # check bug in ruamel.yaml is fixed: raises TypeError: source has undefined order
    self.assertEqual(cm, cm.copy())

  def test_CommentedMapEquality(self):
    cm = CommentedMap((('b', 2),))
    cm.insert(1, 'a', 1, comment="a comment")
    self.assertEqual(cm, {'a':1, 'b': 2})

  def _getTestResource(self):
    resourceDef = {
      "name": "test",
      'a': {'ref': 'name'},
      'b': [1, 2, 3],
      'd': {'a':'va', 'b':'vb'},
      'n': {'n':{'n':'n'}},
      's': {'ref': '.'},
      'x': [{
              'a': [{'c':1}, {'c':2}, {'b': 'exists'}, {'l': ['l1']}, {'l': ['l2']}],
            },
            [{'c':5}],
            {
              "a": [{'c':3}, {'c':4}, {'l': ['l3']}, {'l': ['l4']}]
            },
            [{'c':6}],
          ],
      'e': {'a1':{'b1': 'v1'},
            'a2':{'b2': 'v2'}},
      'f': {'a': 1, 'b': {'ref': '.::f::a'} },
      }
    resource = Resource("test", attributes=resourceDef)
    assert resource.attributes['x'] == resourceDef['x']
    return resource

  def test_refs(self):
    assert Ref.isRef({'ref': '::name'})
    assert not Ref.isRef({'ref': '::name', 'somethingUnexpected': 1})
    assert Ref.isRef({
      'ref': '::name',
      'vars': {
        'a': None
      }
    })

  def test_refPaths(self):
    resource = self._getTestResource()
    for (exp, expected) in [
      ['x?::a[c=4]', [[{'c':3}, {'c':4}, {'l': ['l3']}, {'l': ['l4']}]] ],
      ['x::a[c]?', [[{'c':1}, {'c':2}, {'b': 'exists'}, {'l': ['l1']}, {'l': ['l2']}]]],
      ['x::a::[c]', [{'c':1}, {'c':2}, {'c':3}, {'c':4}]],
      ['x::a?::[c]', [{'c':1}, {'c':2}]],
      ['a', ['test']],
      ['b', [[1,2,3]]],
      ['b::0', [1]],
      ['b?::2', [3]],
      ['[b::2]::b::2', [3]],
      ['b::1', [2]],
      ['s::b', [[1, 2, 3]]],
      ['s::b::1', [2]],
      ['s::s::b::1', [2]],
      ['n::n::n', ['n']],
      ['d[a=va]', [{'a':'va', 'b':'vb'}]],
      ['d[a=vb]', []],
      ['b[1=2]', [[1,2,3]]],
      ['b[1=1]', []],
      ['a[=test]', ['test']],
      ['a[!=test]', []],
      ['a[key]', []],
      ['d[a=va][b=vb]', [{'a':'va', 'b':'vb'}]],
      ['d[a=va][a=vb]', []],
      ['d[a=va][a!=vb]', [{'a':'va', 'b':'vb'}]],
      ['d[a=va]::b', ['vb']],
      ['x::a::c', [1,2,3,4]],
      ['x::c', [5, 6]],
      ['x::[c]', [{'c':5}, {'c':6}]],
      ['x::a[b]::c', [1,2]],
      ['x::a[!b]::c', [3,4]],
      ['x::a::l', [['l1'],['l2'],['l3'],['l4']]],
      [{'ref': 'a[=$yes]',
        'vars': {'yes': 'test'}
       },  ['test']],
      [{'ref': 'a[=$no]',
        'vars': {'no': None}
       },  []],
       ['[a]', [resource]],
       ['[=blah]', []],
       ['[blah]', []],
       ['[!blah]', [resource]],
       ['.[!=blah]', [resource]],
       ['[!a]', []],
       ['::test', [resource]],
       ['d::*', set(['va', 'vb'])],
       ['e::*::b2', ['v2']],
       ["*", []],
       ["f", [{'a': 1, 'b': 1}]],
       # [{"q": "{{ foo }}"}, ["{{ foo }}"]]
       #XXX test nested ['.[k[d=3]=4]']
    ]:
      ref = Ref(exp)
      # print ('eval', ref.source, ref)
      if isinstance(expected, set):
        # for results where order isn't guaranteed in python2.7
        self.assertEqual(set(ref.resolve(RefContext(resource))), expected, "expr was: " + ref.source)
      else:
        self.assertEqual(ref.resolve(RefContext(resource, trace=0)), expected, "expr was: " + ref.source)

  def test_funcs(self):
    resource = self._getTestResource()
    test1 = {
      'ref': '.name',
      'vars': {
        'a': None
      }
    }
    test2 = {
      'ref': '$b',
      'vars': {
        'b': 1
      }
    }
    test3 = {
      'ref': {
        'if': {'not': '$a'},
        'then': {'q': 'expected'},
        'else': {'q': 'unexpected'},
      },
      'vars': {
        'a': None
      }
    }
    result1 = Ref(test1).resolveOne(RefContext(resource))
    self.assertEqual('test', result1)
    result2 = Ref(test2).resolveOne(RefContext(resource))
    self.assertEqual(1, result2)
    result3 = Ref(test3).resolveOne(RefContext(resource))
    self.assertEqual('expected', result3)
    result4 = Ref(test3).resolve(RefContext(resource))
    self.assertEqual(['expected'], result4)
    test5 = {
      'ref': {
        'or': ['$a', 'b'],
      },
      'vars': {
        'a': None
      }
    }
    result5 = Ref(test5).resolveOne(RefContext(resource))
    self.assertEqual(resource.attributes['b'], result5) # this doesn't seem obvious!

  def test_forEach(self):
    return # XXX get foreach working again
    resource = self._getTestResource()
    test1 = {
      'ref': '.',
      'foreach': {
          'value': {'content': { 'ref': 'b'}}
      },
    }
    expected0 = {'content': [1, 2, 3]}
    expected = {
      'test': expected0
    }
    result0 = Ref(test1).resolveOne(RefContext(resource, trace=1))
    self.assertEqual(expected0, result0)
    # resolve has same result as resolveOne
    self.assertEqual([expected0], Ref(test1).resolve(RefContext(resource)))

    #add 'key' to make result a dict
    test1['foreach']['key'] = '.name';
    result1 = Ref(test1).resolve(RefContext(resource))
    self.assertEqual([expected], result1)
    result2 = Ref(test1).resolveOne(RefContext(resource))
    self.assertEqual(expected, result2)

  def test_serializeValues(self):
    resource = self._getTestResource()
    src = {'a': ['b', resource]}
    serialized = serializeValue(src)
    self.assertEqual(serialized, {'a': ['b', {'ref': '::test'}]})
    self.assertEqual(src, mapValue(serialized, resource))

  def test_template(self):
    self.assertEqual(runTemplate(" {{ foo }} ", {"foo": "hello"}), " hello ")
    from giterop.runtime import Resource
    resource = Resource("test", attributes=dict(a1="hello"))
    vars = dict(__giterop = RefContext(resource))
    self.assertEqual(runTemplate(' {{ "::test::a1" | ref }} ', vars), u" hello ")
    self.assertEqual(runTemplate(' {{ lookup("giterup", "::test::a1") }} ', vars), u" hello ")
    self.assertEqual(runTemplate('{{  query("giterup", "::test::a1") }}', vars), [u'hello'])

    os.environ['TEST_ENV'] = 'testEnv' # note: tox doesn't pass on environment variables so we need to set one now
    self.assertEqual(mapValue("{{ lookup('env', 'TEST_ENV') }}", resource), 'testEnv')

    #test that ref vars as can be used as template string vars
    exp = {'a': "{{ aVar }} world"}
    vars = {'aVar': 'hello'}
    self.assertEqual(mapValue(exp, RefContext(resource, vars)), {'a': 'hello world'})

  def test_innerReferences(self):
    resourceDef = {'a':
      dict(b = {"ref": "a::c"},
        c= {'e': 1},
        d= ['2', {"ref": "a::d::0"}]
      )
    }
    resource = Resource("test", attributes=resourceDef)

    expectedA = {'c': {'e': 1}, 'b': {'e': 1}, 'd': ['2', '2']}
    self.assertEqual(resource.attributes['a']['b'], expectedA['b'])
    self.assertEqual(resource.attributes['a'], expectedA)
    self.assertEqual(Ref("a").resolve(RefContext(resource)), [expectedA])
    self.assertEqual(Ref("a").resolveOne(RefContext(resource)), expectedA)

    expected = ['2']
    self.assertEqual(Ref("a::d::0").resolve(RefContext(resource)), expected)
    self.assertEqual(Ref("a::d::1").resolve(RefContext(resource)), expected)

    # print('test_references', resource.attributes,
    #   'AAAA', resource.attributes['a'],
    #   'BBB', resource.attributes['a']['b'],
    # )
    self.assertEqual(resource.attributes['a'], expectedA)
    self.assertEqual(resource.attributes['a']['d'][0], '2')
    self.assertEqual(resource.attributes['a']['d'][1], '2')
    self.assertEqual(resource.attributes['a']['b']['e'], 1)

    self.assertEqual(Ref("a::b::e").resolve(RefContext(resource)), [1])

    # test again to make sure it still resolves correctly
    self.assertEqual(Ref("a::d::0").resolve(RefContext(resource)), expected)
    self.assertEqual(Ref("a::d::1").resolve(RefContext(resource)), expected)

  def test_vars(self):
    # test dereferencing vars
    resource = self._getTestResource()
    query = {
      'eval': "$aDict",
      'vars': {
        "aDict": {
            'aRef': {"eval": '::test'},
            "aTemplate": "{{ true }}"
        }
      }
    }
    result = Ref(query).resolveOne(RefContext(resource))
    self.assertEqual(result, {'aRef': resource, 'aTemplate': True})

  def test_nodeTraversal1(self):
    root = Resource('r2', { "a": [
            dict(ref="::r1::a"), #'r1'
            dict(ref="b") #'r2'
          ],
        "b" : 'r2'
    })
    child = Resource('r1', {'a': dict(ref="b"), 'b': 'r1'}, root)
    ctx = RefContext(root)
    x = [{
      'a': [{'c':1}, {'c':2}],
    }]
    assert x == ResultsList(x, ctx)
    self.assertEqual(Ref('b').resolve(RefContext(child)), ['r1'])
    self.assertEqual(Ref('a').resolve(RefContext(child)), ['r1'])
    self.assertEqual(Ref('a').resolve(RefContext(root)), [['r1', 'r2']])

  def test_nodeTraversal2(self):
    root = Resource('root', {
      'a': [{'ref':'::child'}, {'b': 2}]
    })
    child = Resource('child', {'b': 1}, root)
    self.assertEqual(Ref('.ancestors').resolve(RefContext(child)), [[child, root]])
    # self.assertEqual(Ref('a::b').resolve(RefContext(root)), [1])
    self.assertEqual(Ref('a').resolve(RefContext(child)), [[child, {'b': 2}]])
    # a resolves to [child, dict] so a::b resolves to [child[b], [b]2]
    self.assertEqual(Ref('a::b').resolve(RefContext(child)), [1, 2])

  def test_lookup(self):
    resource = self._getTestResource()
    os.environ['TEST_ENV'] = 'testEnv' # note: tox doesn't pass on environment variables so we need to set one now
    query = {
      "eval": {"lookup": {"env": 'TEST_ENV'}}
    }
    self.assertEqual(mapValue(query, resource), 'testEnv')
