# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

try:
  import json
except ImportError:
  import simplejson as json

import datetime
import time

__docformat__ = "epytext"

class Attr(object):
  """
  Encapsulates information about an attribute in the JSON encoding of the
  object. It identifies properties of the attribute such as whether it's
  read-only, its type, etc.
  """
  DATE_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

  def __init__(self, atype=None, rw=True, is_api_list=False):
    self._atype = atype
    self._is_api_list = is_api_list
    self.rw = rw

  def to_json(self, value, preserve_ro):
    """
    Returns the JSON encoding of the given attribute value.

    If the value has a 'to_json_dict' object, that method is called. Otherwise,
    the following values are returned for each input type:
    - datetime.datetime: string with the API representation of a date.
    - dictionary: if 'atype' is ApiConfig, a list of ApiConfig objects.
    - python list: python list (or ApiList) with JSON encoding of items
    - the raw value otherwise
    """
    if hasattr(value, 'to_json_dict'):
      return value.to_json_dict(preserve_ro)
    elif isinstance(value, dict) and self._atype == ApiConfig:
      return config_to_api_list(value)
    elif isinstance(value, datetime.datetime):
      return value.strftime(self.DATE_FMT)
    elif isinstance(value, list):
      if self._is_api_list:
        return ApiList(value).to_json_dict()
      else:
        return [ self.to_json(x, preserve_ro) for x in value ]
    else:
      return value

  def from_json(self, resource_root, data):
    """
    Parses the given JSON value into an appropriate python object.

    This means:
    - a datetime.datetime if 'atype' is datetime.datetime
    - a converted config dictionary or config list if 'atype' is ApiConfig
    - if the attr is an API list, an ApiList with instances of 'atype'
    - an instance of 'atype' if it has a 'from_json_dict' method
    - a python list with decoded versions of the member objects if the input
      is a python list.
    - the raw value otherwise
    """
    if data is None:
      return None

    if self._atype == datetime.datetime:
      return datetime.datetime.strptime(data, self.DATE_FMT)
    elif self._atype == ApiConfig:
      # ApiConfig is special. We want a python dictionary for summary views,
      # but an ApiList for full views. Try to detect each case from the JSON
      # data.
      if not data['items']:
        return { }
      first = data['items'][0]
      return json_to_config(data, len(first) == 2)
    elif self._is_api_list:
      return ApiList.from_json_dict(self._atype, data, resource_root)
    elif isinstance(data, list):
      return [ self.from_json(resource_root, x) for x in data ]
    elif hasattr(self._atype, 'from_json_dict'):
      return self._atype.from_json_dict(data, resource_root)
    else:
      return data

class ROAttr(Attr):
  """
  Subclass that just defines the attribute as read-only.
  """
  def __init__(self, atype=None, is_api_list=False):
    Attr.__init__(self, atype=atype, rw=False, is_api_list=is_api_list)

class BaseApiObject(object):
  """
  The BaseApiObject helps with (de)serialization from/to JSON.

  The derived class has two ways of defining custom attributes:
  - Overwriting the '_ATTRIBUTES' field with the attribute dictionary
  - Override the _get_attributes() method, in case static initialization of
    the above field is not possible.

  It's recommended that the _get_attributes() implementation do caching to
  avoid computing the dictionary on every invocation.

  The derived class's constructor must call the base class's init() static
  method. All constructor arguments (aside from self and resource_root) must
  be keywords arguments with default values (typically None), or
  from_json_dict() will not work.
  """

  _ATTRIBUTES = { }
  _WHITELIST = ( '_resource_root', '_attributes' )

  @classmethod
  def _get_attributes(cls):
    """
    Returns a map of property names to attr instances (or None for default
    attribute behavior) describing the properties of the object.

    By default, this method will return the class's _ATTRIBUTES field.
    Classes can override this method to do custom initialization of the
    attributes when needed.
    """
    return cls._ATTRIBUTES

  @staticmethod
  def init(obj, resource_root, attrs=None):
    """
    Wraper around the real constructor to avoid issues with the 'self'
    argument. Call like this, from a subclass's constructor:

      BaseApiObject.init(self, locals())
    """
    # This works around http://bugs.python.org/issue2646
    # We use unicode strings as keys in kwargs.
    str_attrs = { }
    if attrs:
      for k, v in attrs.iteritems():
        if k not in ('self', 'resource_root'):
          str_attrs[k] = v
    BaseApiObject.__init__(obj, resource_root, **str_attrs)

  def __init__(self, resource_root, **attrs):
    """
    Initializes internal state and sets all known writable properties of the
    object to None. Then initializes the properties given in the provided
    attributes dictionary.

    @param resource_root: API resource object.
    @param attrs: optional dictionary of attributes to set. This should only
                  contain r/w attributes.
    """
    self._resource_root = resource_root

    for name, attr in self._get_attributes().iteritems():
      if not attr or attr.rw:
        object.__setattr__(self, name, None)
    if attrs:
      self._set_attrs(attrs, from_json=False)

  def _set_attrs(self, attrs, allow_ro=False, from_json=True):
    """
    Sets all the attributes in the dictionary. Optionally, allows setting
    read-only attributes (e.g. when deserializing from JSON) and skipping
    JSON deserialization of values.
    """
    for k, v in attrs.iteritems():
      attr = self._check_attr(k, allow_ro)
      if attr and from_json:
        v = attr.from_json(self._get_resource_root(), v)
      object.__setattr__(self, k, v)

  def __setattr__(self, name, val):
    if name not in BaseApiObject._WHITELIST:
      self._check_attr(name, False)
    object.__setattr__(self, name, val)

  def _check_attr(self, name, allow_ro):
    if name not in self._get_attributes():
      raise AttributeError('Invalid property %s for class %s.' %
          (name, self.__class__.__name__))
    attr = self._get_attributes()[name]
    if not allow_ro and attr and not attr.rw:
      raise AttributeError('Attribute %s of class %s is read only.' %
          (name, self.__class__.__name__))
    return attr

  def _get_resource_root(self):
    return self._resource_root

  def _update(self, api_obj):
    """Copy state from api_obj to this object."""
    if not isinstance(self, api_obj.__class__):
      raise ValueError(
          "Class %s does not derive from %s; cannot update attributes." %
          (self.__class__, api_obj.__class__))

    for name in self._get_attributes().keys():
      try:
        val = getattr(api_obj, name)
        setattr(self, name, val)
      except AttributeError, ignored:
        pass

  def to_json_dict(self, preserve_ro=False):
    dic = { }
    for name, attr in self._get_attributes().iteritems():
      if not preserve_ro and attr and not attr.rw:
        continue
      try:
        value = getattr(self, name)
        if attr:
          dic[name] = attr.to_json(value, preserve_ro)
        else:
          dic[name] = value
      except AttributeError:
        pass
    return dic

  def __str__(self):
    """
    Default implementation of __str__. Uses the type name and the first
    attribute retrieved from the attribute map to create the string.
    """
    name = self._get_attributes().keys()[0]
    value = getattr(self, name, None)
    return "<%s>: %s = %s" % (self.__class__.__name__, name, value)

  @classmethod
  def from_json_dict(cls, dic, resource_root):
    obj = cls(resource_root)
    obj._set_attrs(dic, allow_ro=True)
    return obj

  def _require_min_api_version(self, version):
    """
    Raise an exception if the version of the api is less than the given version.

    @param version: The minimum required version.
    """
    actual_version = self._get_resource_root().version
    if actual_version < version:
      raise Exception("API version %s is required but %s is in use."
          % (version, actual_version))

class ApiList(object):
  """A list of some api object"""
  LIST_KEY = "items"

  def __init__(self, objects):
    self.objects = objects

  def __str__(self):
    return "<ApiList>(%d): [%s]" % (
        len(self.objects),
        ", ".join([str(item) for item in self.objects]))

  def to_json_dict(self):
    return { ApiList.LIST_KEY :
            [ x.to_json_dict() for x in self.objects ] }

  def __len__(self):
    return self.objects.__len__()

  def __iter__(self):
    return self.objects.__iter__()

  def __getitem__(self, i):
    return self.objects.__getitem__(i)

  def __getslice(self, i, j):
    return self.objects.__getslice__(i, j)

  @staticmethod
  def from_json_dict(member_cls, dic, resource_root):
    json_list = dic[ApiList.LIST_KEY]
    objects = [ member_cls.from_json_dict(x, resource_root) for x in json_list ]
    return ApiList(objects)


class ApiHostRef(BaseApiObject):
  _ATTRIBUTES = {
    'hostId'  : None,
  }

  def __init__(self, resource_root, hostId=None):
    BaseApiObject.init(self, resource_root, locals())

  def __str__(self):
    return "<ApiHostRef>: %s" % (self.hostId)

class ApiServiceRef(BaseApiObject):
  _ATTRIBUTES = {
    'clusterName' : None,
    'serviceName' : None,
    'peerName'    : None,
  }

  def __init__(self, resource_root, serviceName=None, clusterName=None,
      peerName=None):
    BaseApiObject.init(self, resource_root, locals())

class ApiClusterRef(BaseApiObject):
  _ATTRIBUTES = {
    'clusterName' : None,
  }

  def __init__(self, resource_root, clusterName = None):
    BaseApiObject.init(self, resource_root, locals())

class ApiRoleRef(BaseApiObject):
  _ATTRIBUTES = {
    'clusterName' : None,
    'serviceName' : None,
    'roleName'    : None,
  }

  def __init__(self, resource_root, serviceName=None, roleName=None,
      clusterName=None):
    BaseApiObject.init(self, resource_root, locals())

class ApiRoleConfigGroupRef(BaseApiObject):
  _ATTRIBUTES = {
    'roleConfigGroupName' : None,
  }

  def __init__(self, resource_root, roleConfigGroupName=None):
    BaseApiObject.init(self, resource_root, locals())

class ApiCommand(BaseApiObject):
  SYNCHRONOUS_COMMAND_ID = -1

  @classmethod
  def _get_attributes(cls):
    if not cls.__dict__.has_key('_ATTRIBUTES'):
      cls._ATTRIBUTES = {
        'id'            : ROAttr(),
        'name'          : ROAttr(),
        'startTime'     : ROAttr(datetime.datetime),
        'endTime'       : ROAttr(datetime.datetime),
        'active'        : ROAttr(),
        'success'       : ROAttr(),
        'resultMessage' : ROAttr(),
        'clusterRef'    : ROAttr(ApiClusterRef),
        'serviceRef'    : ROAttr(ApiServiceRef),
        'roleRef'       : ROAttr(ApiRoleRef),
        'hostRef'       : ROAttr(ApiHostRef),
        'children'      : ROAttr(ApiCommand, is_api_list=True),
        'parent'        : ROAttr(ApiCommand),
        'resultDataUrl' : ROAttr(),
      }
    return cls._ATTRIBUTES

  def __str__(self):
    return "<ApiCommand>: '%s' (id: %s; active: %s; success: %s)" % (
        self.name, self.id, self.active, self.success)

  def _path(self):
    return '/commands/%d' % self.id

  def fetch(self):
    """
    Retrieve updated data about the command from the server.

    @param resource_root: The root Resource object.
    @return: A new ApiCommand object.
    """
    if self.id == ApiCommand.SYNCHRONOUS_COMMAND_ID:
      return self

    resp = self._get_resource_root().get(self._path())
    return ApiCommand.from_json_dict(resp, self._get_resource_root())

  def wait(self, timeout=None):
    """
    Wait for command to finish.

    @param timeout: (Optional) Max amount of time (in seconds) to wait. Wait
                    forever by default.
    @return: The final ApiCommand object, containing the last known state.
             The command may still be running in case of timeout.
    """
    if self.id == ApiCommand.SYNCHRONOUS_COMMAND_ID:
      return self

    SLEEP_SEC = 5

    if timeout is None:
      deadline = None
    else:
      deadline = time.time() + timeout

    while True:
      cmd = self.fetch()
      if not cmd.active:
        return cmd

      if deadline is not None:
        now = time.time()
        if deadline < now:
          return cmd
        else:
          time.sleep(min(SLEEP_SEC, deadline - now))
      else:
        time.sleep(SLEEP_SEC)


  def abort(self):
    """
    Abort a running command.

    @param resource_root: The root Resource object.
    @return: A new ApiCommand object with the updated information.
    """
    if self.id == ApiCommand.SYNCHRONOUS_COMMAND_ID:
      return self

    path = self._path() + '/abort'
    resp = self._get_resource_root().post(path)
    return ApiCommand.from_json_dict(resp, self._get_resource_root())

#
# Metrics.
#

class ApiMetricData(BaseApiObject):
  """Metric reading data."""

  _ATTRIBUTES = {
    'timestamp' : ROAttr(datetime.datetime),
    'value'     : ROAttr(),
  }

  def __init__(self, resource_root):
    BaseApiObject.init(self, resource_root)


class ApiMetric(BaseApiObject):
  """Metric information."""

  _ATTRIBUTES = {
    'name'        : ROAttr(),
    'context'     : ROAttr(),
    'unit'        : ROAttr(),
    'data'        : ROAttr(ApiMetricData),
    'displayName' : ROAttr(),
    'description' : ROAttr(),
  }

  def __init__(self, resource_root):
    BaseApiObject.init(self, resource_root)

#
# Activities.
#

class ApiActivity(BaseApiObject):
  _ATTRIBUTES = {
    'name'              : ROAttr(),
    'type'              : ROAttr(),
    'parent'            : ROAttr(),
    'startTime'         : ROAttr(),
    'finishTime'        : ROAttr(),
    'id'                : ROAttr(),
    'status'            : ROAttr(),
    'user'              : ROAttr(),
    'group'             : ROAttr(),
    'inputDir'          : ROAttr(),
    'outputDir'         : ROAttr(),
    'mapper'            : ROAttr(),
    'combiner'          : ROAttr(),
    'reducer'           : ROAttr(),
    'queueName'         : ROAttr(),
    'schedulerPriority' : ROAttr(),
  }

  def __init__(self, resource_root):
    BaseApiObject.init(self, resource_root)

  def __str__(self):
    return "<ApiActivity>: %s (%s)" % (self.name, self.status)

#
# Replication
#

class ApiCmPeer(BaseApiObject):
  _ATTRIBUTES = {
      'name'      : None,
      'url'       : None,
      'username'  : None,
      'password'  : None,
    }

  def __str__(self):
    return "<ApiPeer>: %s (%s)" % (self.name, self.url)

class ApiHdfsReplicationArguments(BaseApiObject):
  _ATTRIBUTES = {
    'sourceService'             : Attr(ApiServiceRef),
    'sourcePath'                : None,
    'destinationPath'           : None,
    'mapreduceServiceName'      : None,
    'userName'                  : None,
    'numMaps'                   : None,
    'dryRun'                    : None,
    'schedulerPoolName'         : None,
    'abortOnError'              : None,
    'preservePermissions'       : None,
    'preserveBlockSize'         : None,
    'preserveReplicationCount'  : None,
    'removeMissingFiles'        : None,
  }

class ApiHiveTable(BaseApiObject):
  _ATTRIBUTES = {
    'database'  : None,
    'tableName' : None,
  }

  def __str__(self):
    return "<ApiHiveTable>: %s, %s" % (self.database, self.tableName)

class ApiHiveReplicationArguments(BaseApiObject):
  _ATTRIBUTES = {
    'sourceService' : Attr(ApiServiceRef),
    'tableFilters'  : Attr(ApiHiveTable),
    'exportDir'     : None,
    'force'         : None,
    'replicateData' : None,
    'hdfsArguments' : Attr(ApiHdfsReplicationArguments),
    'dryRun'        : None,
  }

class ApiReplicationSchedule(BaseApiObject):
  _ATTRIBUTES = {
    'startTime'       : Attr(datetime.datetime),
    'endTime'         : Attr(datetime.datetime),
    'interval'        : None,
    'intervalUnit'    : None,
    'paused'          : None,
    'hdfsArguments'   : Attr(ApiHdfsReplicationArguments),
    'hiveArguments'   : Attr(ApiHiveReplicationArguments),
    'alertOnStart'    : None,
    'alertOnSuccess'  : None,
    'alertOnFail'     : None,
    'alertOnAbort'    : None,
    'id'              : ROAttr(),
    'nextRun'         : ROAttr(datetime.datetime),
    'history'         : ROAttr(),
  }

#
# Configuration helpers.
#

class ApiConfig(BaseApiObject):
  _ATTRIBUTES = {
    'name'              : None,
    'value'             : None,
    'required'          : ROAttr(),
    'default'           : ROAttr(),
    'displayName'       : ROAttr(),
    'description'       : ROAttr(),
    'relatedName'       : ROAttr(),
    'validationState'   : ROAttr(),
    'validationMessage' : ROAttr(),
  }

  def __init__(self, resource_root, name=None, value=None):
    BaseApiObject.init(self, resource_root, locals())

  def __str__(self):
    return "<ApiConfig>: %s = %s" % (self.name, self.value)

def config_to_api_list(dic):
  """
  Converts a python dictionary into a list containing the proper
  ApiConfig encoding for configuration data.

  @param dic Key-value pairs to convert.
  @return JSON dictionary of an ApiConfig list (*not* an ApiList).
  """
  config = [ ]
  for k, v in dic.iteritems():
    config.append({ 'name' : k, 'value': v })
  return { ApiList.LIST_KEY : config }

def config_to_json(dic):
  """
  Converts a python dictionary into a JSON payload.

  The payload matches the expected "apiConfig list" type used to update
  configuration parameters using the API.

  @param dic Key-value pairs to convert.
  @return String with the JSON-encoded data.
  """
  return json.dumps(config_to_api_list(dic))

def json_to_config(dic, full = False):
  """
  Converts a JSON-decoded config dictionary to a python dictionary.

  When materializing the full view, the values in the dictionary will be
  instances of ApiConfig, instead of strings.

  @param dic JSON-decoded config dictionary.
  @param full Whether to materialize the full view of the config data.
  @return Python dictionary with config data.
  """
  config = { }
  for entry in dic['items']:
    k = entry['name']
    if full:
      config[k] = ApiConfig.from_json_dict(entry, None)
    else:
      config[k] = entry.get('value')
  return config
