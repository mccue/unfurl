"""
TOSCA implementation

Differences with TOSCA 1.1:

 * Entity type can allow properties that don't need to be declared
 * Added "any" datatype
 * Interface "implementation" values can be a node template name, and the corresponding
   instance will be used to execute the operation.
"""
from .tosca_plugins import TOSCA_VERSION
from .util import UnfurlValidationError
from .eval import Ref
from .yamlloader import resolvePathToToscaImport
from toscaparser.tosca_template import ToscaTemplate

# from toscaparser.topology_template import TopologyTemplate
from toscaparser.elements.capabilitytype import CapabilityTypeDef
from toscaparser.common.exception import ExceptionCollector, ValidationError
import logging

logger = logging.getLogger("unfurl")

from toscaparser import functions


class RefFunc(functions.Function):
    def result(self):
        return {self.name: self.args}

    def validate(self):
        pass


functions.function_mappings["eval"] = RefFunc
functions.function_mappings["ref"] = RefFunc

toscaIsFunction = functions.is_function


def is_function(function):
    return toscaIsFunction(function) or Ref.isRef(function)


functions.is_function = is_function


def createDefaultTopology():
    tpl = dict(
        tosca_definitions_version=TOSCA_VERSION,
        topology_template=dict(
            node_templates={"_default": {"type": "tosca.nodes.Root"}},
            relationship_templates={"_default": {"type": "tosca.relationships.Root"}},
        ),
    )
    return ToscaTemplate(yaml_dict_tpl=tpl).topology_template


class ToscaSpec(object):
    ConfiguratorType = "unfurl.nodes.Configurator"
    InstallerType = "unfurl.nodes.Installer"

    def __init__(self, toscaDef, inputs=None, instances=None, path=None):
        topology_tpl = toscaDef.get("topology_template")
        if not topology_tpl:
            toscaDef["topology_template"] = dict(
                node_templates={}, relationship_templates={}
            )
        else:
            for section in ["node_templates", "relationship_templates"]:
                if not topology_tpl.get(section):
                    topology_tpl[section] = {}

        if instances:
            self.loadInstances(toscaDef, instances)

        logger.info("Validating TOSCA template at %s", path)
        try:
            # need to set a path for the import loader
            self.template = ToscaTemplate(
                path=path, parsed_params=inputs, yaml_dict_tpl=toscaDef
            )
        except ValidationError:
            message = "\n".join(ExceptionCollector.getExceptionsReport(False))
            raise UnfurlValidationError(
                "TOSCA validation failed for %s: \n%s" % (path, message),
                ExceptionCollector.getExceptions(),
            )

        self.nodeTemplates = {}
        self.installers = {}
        self.relationshipTemplates = {}
        if hasattr(self.template, "nodetemplates"):
            for template in self.template.nodetemplates:
                nodeTemplate = NodeSpec(template)
                if template.is_derived_from(self.InstallerType):
                    self.installers[template.name] = nodeTemplate
                self.nodeTemplates[template.name] = nodeTemplate
        self.topology = TopologySpec(self.template.topology_template, inputs)

    def resolveArtifactPath(self, artifact_tpl, path=None):
        return resolvePathToToscaImport(path or self.template.path,
                          self.template.tpl, artifact_tpl)

    def getTemplate(self, name):
        if name == "#topology":
            return self.topology
        return self.nodeTemplates.get(name, self.relationshipTemplates.get(name))

    def loadInstances(self, toscaDef, tpl):
        """
    Creates node templates for any instances defined in the spec

    .. code-block:: YAML

      spec:
            instances:
              test:
                install: test
            installers:
              test:
                operations:
                  default:
                    implementation: TestConfigurator
                    inputs:
"""
        node_templates = toscaDef["topology_template"]["node_templates"]
        for name, impl in tpl.get("installers", {}).items():
            if name not in node_templates:
                node_templates[name] = dict(type=self.InstallerType, properties=impl)
            else:
                raise UnfurlValidationError(
                    'can not add installer "%s", there is already a node template with that name'
                    % name
                )

        for name, impl in tpl.get("instances", {}).items():
            if name not in node_templates and impl is not None:
                node_templates[name] = self.loadInstance(impl)

    def loadInstance(self, impl):
        template = {
            "type": impl.get("type", "unfurl.nodes.Default"),
            "properties": impl.get("properties", {}),
        }
        installer = impl.get("install")
        if installer:
            template["requirements"] = [{"install": installer}]
        return template


_defaultTopology = createDefaultTopology()

# represents a node, capability or relationship
class EntitySpec(object):
    def __init__(self, toscaNodeTemplate):
        self.toscaEntityTemplate = toscaNodeTemplate
        self.name = toscaNodeTemplate.name
        self.type = toscaNodeTemplate.type
        # nodes have both properties and attributes
        # as do capability properties and relationships
        # but only property values are declared
        self.properties = {
            prop.name: prop.value for prop in toscaNodeTemplate.get_properties_objects()
        }
        if toscaNodeTemplate.type_definition:
            attrDefs = toscaNodeTemplate.type_definition.get_attributes_def_objects()
            self.defaultAttributes = {
                prop.name: prop.default for prop in attrDefs if prop.default is not None
            }
            propDefs = toscaNodeTemplate.type_definition.get_properties_def()
            propDefs.update(toscaNodeTemplate.type_definition.get_attributes_def())
            self.attributeDefs = propDefs
        else:
            self.defaultAttributes = {}
            self.attributeDefs = {}

    def getInterfaces(self):
        return self.toscaEntityTemplate.interfaces

    def isCompatibleTarget(self, targetStr):
        if self.name == targetStr:
            return True
        return self.toscaEntityTemplate.is_derived_from(targetStr)

    def isCompatibleType(self, typeStr):
        return self.toscaEntityTemplate.is_derived_from(typeStr)

    def getUri(self):
        return self.name  # XXX

    def __repr__(self):
        return "EntitySpec('%s')" % self.name


class NodeSpec(EntitySpec):
    # has attributes: tosca_id, tosca_name, state, (3.4.1 Node States p.61)
    def __init__(self, template=None):
        if not template:
            template = _defaultTopology.nodetemplates[0]
        EntitySpec.__init__(self, template)

    def getRequirements(self, name):
        return [
            req[name] for req in self.toscaEntityTemplate.requirements if name in req
        ]


class RelationshipSpec(EntitySpec):
    # has attributes: tosca_id, tosca_name
    def __init__(self, template=None):
        if not template:
            template = _defaultTopology.relationship_templates[0]
        EntitySpec.__init__(self, template)


class TopologySpec(EntitySpec):
    # has attributes: tosca_id, tosca_name, state, (3.4.1 Node States p.61)
    def __init__(self, template=None, inputs=None):
        if not template:
            template = _defaultTopology
        inputs = inputs or {}

        self.toscaEntityTemplate = template
        self.name = "#topology"
        self.inputs = {
            input.name: inputs.get(input.name, input.default)
            for input in template.inputs
        }
        self.outputs = {output.name: output.value for output in template.outputs}
        self.properties = {}
        self.defaultAttributes = {}

    def getInterfaces(self):
        # doesn't have any interfaces
        return []


# capabilities.Capability isn't an EntityTemplate but duck types with it
class CapabilitySpec(EntitySpec):
    def __init__(self, name=None, nodeTemplate=None):
        if not nodeTemplate:
            self.nodeTemplate = _defaultTopology.nodetemplates[0]
            cap = self.nodeTemplate.get_capabilities_objects()[0]
            name = "feature"
        else:
            self.nodeTemplate = nodeTemplate
            cap = nodeTemplate.get_capabilities()[name]
        self.type = nodeTemplate.type_definition.get_capabilities().get(
            name, CapabilityTypeDef(name, "tosca.capabilities.Node", nodeTemplate.type)
        )
        EntitySpec.__init__(self, cap)

    def isCompatibleTarget(self, targetStr):
        if self.name == targetStr:
            return True
        return self.type.is_derived_from(targetStr)

    def isCompatibleType(self, typeStr):
        return self.type.is_derived_from(typeStr)

    def getInterfaces(self):
        # capabilities don't have their own interfaces
        return self.nodeTemplate.interfaces
