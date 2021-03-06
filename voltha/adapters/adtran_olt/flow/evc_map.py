# Copyright 2017-present Adtran, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import xmltodict
import re
import structlog
from enum import Enum
from twisted.internet.defer import inlineCallbacks, returnValue, succeed

log = structlog.get_logger()

# NOTE: For the EVC Map name, the ingress-port number is the VOLTHA port number (not pon-id since
#       it covers NNI ports as well in order to handle the NNI-NNI case.  For flows that
#       cover an entire pon, the name will have the ONU ID and GEM ID appended to it upon
#       installation with a period as a separator.

EVC_MAP_NAME_FORMAT = 'VOLTHA-{}-{}'   # format(ingress-port, flow.id)
EVC_MAP_NAME_REGEX_ALL = 'VOLTHA-*'


class EVCMap(object):
    """
    Class to wrap EVC functionality
    """
    class EvcConnection(Enum):
        NO_EVC_CONNECTION = 0
        EVC = 1
        DISCARD = 2
        DEFAULT = NO_EVC_CONNECTION

        @staticmethod
        def xml(value):
            # Note we do not have XML for 'EVC' enumeration.
            if value is None:
                value = EVCMap.EvcConnection.DEFAULT
            if value == EVCMap.EvcConnection.DISCARD:
                return '<no-evc-connection/>'
            elif value == EVCMap.EvcConnection.DISCARD:
                return 'discard/'
            raise ValueError('Invalid EvcConnection enumeration')

    class PriorityOption(Enum):
        INHERIT_PRIORITY = 0
        EXPLICIT_PRIORITY = 1
        DEFAULT = INHERIT_PRIORITY

        @staticmethod
        def xml(value):
            if value is None:
                value = EVCMap.PriorityOption.DEFAULT
            if value == EVCMap.PriorityOption.INHERIT_PRIORITY:
                return '<inherit-pri/>'
            elif value == EVCMap.PriorityOption.EXPLICIT_PRIORITY:
                return '<explicit-pri/>'
            raise ValueError('Invalid PriorityOption enumeration')

    def __init__(self, flow, evc, is_ingress_map):
        self._flow = flow
        self._evc = evc
        self._gem_ids_and_vid = None   # { key -> onu-id, value -> tuple(sorted GEM Port IDs, onu_vid) }
        self._is_ingress_map = is_ingress_map
        self._pon_id = None
        self._installed = False
        self._status_message = None

        self._name = None
        self._enabled = True
        self._uni_port = None
        self._evc_connection = EVCMap.EvcConnection.DEFAULT
        self._evc_name = None
        self._men_priority = EVCMap.PriorityOption.DEFAULT
        self._men_pri = 0  # If Explicit Priority

        self._c_tag = None
        self._men_ctag_priority = EVCMap.PriorityOption.DEFAULT
        self._men_ctag_pri = 0  # If Explicit Priority

        self._match_ce_vlan_id = None
        self._match_untagged = False
        self._match_destination_mac_address = None
        self._match_l2cp = False
        self._match_broadcast = False
        self._match_multicast = False
        self._match_unicast = False
        self._match_igmp = False

        # ACL logic
        self._eth_type = None
        self._ip_protocol = None
        self._ipv4_dst = None
        self._udp_dst = None
        self._udp_src = None

        try:
            self._valid = self._decode()

        except Exception as e:
            log.exception('decode', e=e)
            self._valid = False

        if self._valid:
            evc.add_evc_map(self)
        else:
            self._evc = None

    def __str__(self):
        return "EVCMap-{}: UNI: {}, isACL: {}".format(self._name, self._uni_port,
                                                      self._needs_acl_support)

    @staticmethod
    def create_ingress_map(flow, evc):
        return EVCMap(flow, evc, True)

    @staticmethod
    def create_egress_map(flow, evc):
        return EVCMap(flow, evc, False)

    @property
    def valid(self):
        return self._valid

    @property
    def installed(self):
        return self._installed

    @installed.setter
    def installed(self, value):
        assert not value, 'installed can only be reset'                # Can only reset
        self._installed = False

    @property
    def name(self):
        return self._name

    @property
    def status(self):
        return self._status_message

    @status.setter
    def status(self, value):
        self._status_message = value

    @property
    def _needs_acl_support(self):
        return self._eth_type is not None or self._ip_protocol is not None or\
                self._ipv4_dst is not None or self._udp_dst is not None or self._udp_src is not None

    @property
    def pon_id(self):
        return self._pon_id     # May be None

    @property
    def onu_ids(self):
        return self._gem_ids_and_vid.keys()

    @property
    def gem_ids_and_vid(self):
        return self._gem_ids_and_vid.copy()

    @staticmethod
    def _xml_header(operation=None):
        return '<evc-maps xmlns="http://www.adtran.com/ns/yang/adtran-evc-maps"{}><evc-map>'.\
            format('' if operation is None else ' xc:operation="{}"'.format(operation))

    @staticmethod
    def _xml_trailer():
        return '</evc-map></evc-maps>'

    def _common_install_xml(self):
        xml = '<enabled>{}</enabled>'.format('true' if self._enabled else 'false')
        xml += '<uni>{}</uni>'.format(self._uni_port)

        if self._evc_name is not None:
            xml += '<evc>{}</evc>'.format(self._evc_name)
        else:
            xml += EVCMap.EvcConnection.xml(self._evc_connection)

        xml += '<match-untagged>{}</match-untagged>'.format('true'
                                                            if self._match_untagged
                                                            else 'false')
        # if self._c_tag is not None:
        #     xml += '<ctag>{}</ctag>'.format(self._c_tag)
        # TODO: The following is not yet supported (and in some cases, not decoded)
        # self._men_priority = EVCMap.PriorityOption.INHERIT_PRIORITY
        # self._men_pri = 0  # If Explicit Priority
        #
        # self._men_ctag_priority = EVCMap.PriorityOption.INHERIT_PRIORITY
        # self._men_ctag_pri = 0  # If Explicit Priority
        #
        # self._match_ce_vlan_id = None
        # self._match_untagged = True
        # self._match_destination_mac_address = None
        # self._eth_type = None
        # self._ip_protocol = None
        # self._ipv4_dst = None
        # self._udp_dst = None
        # self._udp_src = None
        return xml

    def _ingress_install_xml(self, onu_s_gem_ids_and_vid):
        from ..onu import Onu

        xml = '<evc-maps xmlns="http://www.adtran.com/ns/yang/adtran-evc-maps">'

        for onu_or_vlan_id, gem_ids_and_vid in onu_s_gem_ids_and_vid.iteritems():
            first_gem_id = True
            vid = gem_ids_and_vid[1]
            ident = '{}.{}'.format(self._pon_id, onu_or_vlan_id) if vid is None \
                else onu_or_vlan_id

            for gem_id in gem_ids_and_vid[0]:
                xml += '<evc-map>'
                xml += '<name>{}.{}.{}</name>'.format(self.name, ident, gem_id)
                xml += '<ce-vlan-id>{}</ce-vlan-id>'.format(Onu.gem_id_to_gvid(gem_id))

                # GEM-IDs are a sorted list (ascending). First gemport handles downstream traffic
                if first_gem_id and vid is not None:
                    first_gem_id = False
                    xml += '<network-ingress-filter>'
                    xml += '<men-ctag>{}</men-ctag>'.format(vid)  # Added in August 2017 model
                    xml += '</network-ingress-filter>'

                xml += self._common_install_xml()
                xml += '</evc-map>'
        xml += '</evc-maps>'
        return xml

    def _egress_install_xml(self):
        xml = EVCMap._xml_header()
        xml += '<name>{}</name>'.format(self.name)
        xml += self._common_install_xml()
        xml += EVCMap._xml_trailer()
        return xml

    @inlineCallbacks
    def install(self):
        if self._valid and not self._installed and len(self._gem_ids_and_vid) > 0:
            try:
                # TODO: create generator of XML once we have MANY to install at once
                map_xml = self._ingress_install_xml(self._gem_ids_and_vid) \
                    if self._is_ingress_map else self._egress_install_xml()

                log.debug('install', xml=map_xml, name=self.name)
                results = yield self._flow.handler.netconf_client.edit_config(map_xml,
                                                                              lock_timeout=30)
                self._installed = results.ok
                self.status = '' if results.ok else results.error

            except Exception as e:
                log.exception('install', name=self.name, e=e)
                raise

        returnValue(self._installed and self._valid)

    def _ingress_remove_xml(self, onus_gem_ids_and_vid):
        xml = '<evc-maps xmlns="http://www.adtran.com/ns/yang/adtran-evc-maps"' + \
              ' xc:operation="delete">'

        for onu_id, gem_ids_and_vid in onus_gem_ids_and_vid.iteritems():
            for gem_id in gem_ids_and_vid[0]:
                xml += '<evc-map>'
                xml += '<name>{}.{}.{}</name>'.format(self.name, onu_id, gem_id)
                xml += '</evc-map>'
        xml += '</evc-maps>'
        return xml

    def _egress_remove_xml(self):
        return EVCMap._xml_header('delete') + \
               '<name>{}</name>'.format(self.name) + EVCMap._xml_trailer()

    @inlineCallbacks
    def remove(self):
        if not self.installed:
            returnValue(succeed('Not installed'))

        log.info('removing', evc_map=self)

        def _success(rpc_reply):
            log.debug('remove-success', rpc_reply=rpc_reply)
            self._installed = False

        def _failure(failure):
            log.error('remove-failed', failure=failure)
            self._installed = False

        # TODO: create generator of XML once we have MANY to install at once
        map_xml = self._ingress_remove_xml(self._gem_ids_and_vid) if self._is_ingress_map \
            else self._egress_remove_xml()

        d = self._flow.handler.netconf_client.edit_config(map_xml, lock_timeout=30)
        d.addCallbacks(_success, _failure)
        return d

    @inlineCallbacks
    def delete(self):
        """
        Remove from hardware and delete/clean-up EVC-MAP Object
        """
        if self._evc is not None:
            self._evc.remove_evc_map(self)
            self._evc = None

        self._flow = None
        self._valid = False

        try:
            yield self.remove()

        except Exception as e:
            log.exception('removal', e=e)

        returnValue('Done')

    @staticmethod
    def create_evc_map_name(flow):
        return EVC_MAP_NAME_FORMAT.format(flow.in_port, flow.flow_id)

    def _decode(self):
        from evc import EVC
        from flow_entry import FlowEntry

        flow = self._flow  # TODO: Drop saving of flow once debug complete

        self._name = EVCMap.create_evc_map_name(flow)

        if self._evc:
            self._evc_connection = EVCMap.EvcConnection.EVC
            self._evc_name = self._evc.name
        else:
            self._status_message = 'Can only create EVC-MAP if EVC supplied'
            return False

        is_pon = flow.handler.is_pon_port(flow.in_port)
        is_uni = flow.handler.is_uni_port(flow.in_port)

        if is_pon or is_uni:
            self._uni_port = flow.handler.get_port_name(flow.in_port)
            self._evc.ce_vlan_preservation = False
        else:
            self._status_message = 'EVC-MAPS without UNI or PON ports are not supported'
            return False    # UNI Ports handled in the EVC Maps

        # ACL logic

        self._eth_type = flow.eth_type

        if self._eth_type == FlowEntry.EtherType.IPv4.value:
            self._ip_protocol = flow.ip_protocol
            self._ipv4_dst = flow.ipv4_dst

            if self._ip_protocol == FlowEntry.IpProtocol.UDP.value:
                self._udp_dst = flow.udp_dst
                self._udp_src = flow.udp_src

        # If no match of VLAN this may be for untagged traffic or upstream and needs to
        # match the gem-port vid

        if self._is_ingress_map and is_pon:
            pon_port = flow.handler.get_southbound_port(flow.in_port)

            if pon_port is not None:
                self._pon_id = pon_port.pon_id
                self._gem_ids_and_vid = pon_port.gem_ids(flow.logical_port,
                                                         self._needs_acl_support,
                                                         flow.is_multicast_flow)

                # TODO: Only EAPOL ACL support for the first demo - FIXED_ONU
                if self._needs_acl_support and self._eth_type != FlowEntry.EtherType.EAPOL.value:
                    self._gem_ids_and_vid = dict()

        # self._match_untagged = flow.vlan_id is None and flow.inner_vid is None
        self._c_tag = flow.inner_vid

        # If a push of a single VLAN is present with a POP of the VLAN in the EVC's
        # flow, then this is a traditional EVC flow

        self._evc.men_to_uni_tag_manipulation = EVC.Men2UniManipulation.POP_OUT_TAG_ONLY
        self._evc.switching_method = EVC.SwitchingMethod.DOUBLE_TAGGED

        # if len(flow.push_vlan_id) == 1 and self._evc.flow_entry.pop_vlan == 1:
        #     self._evc.men_to_uni_tag_manipulation = EVC.Men2UniManipulation.SYMMETRIC
        #     self._evc.switching_method = EVC.SwitchingMethod.SINGLE_TAGGED
        #     self._evc.stpid = flow.push_vlan_tpid[0]
        #
        # elif len(flow.push_vlan_id) == 2 and self._evc.flow_entry.pop_vlan == 1:
        #     self._evc.men_to_uni_tag_manipulation = EVC.Men2UniManipulation.POP_OUT_TAG_ONLY
        #     self._evc.switching_method = EVC.SwitchingMethod.DOUBLE_TAGGED
        #     # self._match_ce_vlan_id = 'something maybe'
        #     raise NotImplementedError('TODO: Not supported/needed yet')

        return True

    # Bulk operations

    @staticmethod
    def remove_all(client, regex_=EVC_MAP_NAME_REGEX_ALL):
        """
        Remove all matching EVC Maps from hardware

        :param client: (ncclient) NETCONF Client to use
        :param regex_: (String) Regular expression for name matching
        :return: (deferred)
        """
        # Do a 'get' on the evc-map config an you should get the names
        get_xml = """
        <filter>
          <evc-maps xmlns="http://www.adtran.com/ns/yang/adtran-evc-maps">
            <evc-map>
              <name/>
            </evc-map>
          </evc-maps>
        </filter>
        """
        log.info('query', xml=get_xml, regex=regex_)

        def request_failed(results, operation):
            log.error('{}-failed'.format(operation), results=results)
            # No further actions. Periodic poll later on will scrub any old EVC-Maps if needed

        def delete_complete(results):
            log.debug('delete-complete', results=results)

        def do_delete(rpc_reply, regexpr):
            log.debug('query-complete', rpc_reply=rpc_reply)

            if rpc_reply.ok:
                result_dict = xmltodict.parse(rpc_reply.data_xml)
                entries = result_dict['data']['evc-maps'] if 'evc-maps' in result_dict['data'] else {}

                if 'evc-map' in entries:
                    p = re.compile(regexpr)

                    if isinstance(entries['evc-map'], list):
                        names = {entry['name'] for entry in entries['evc-map']
                                 if 'name' in entry and p.match(entry['name'])}
                    else:
                        names = set()
                        for item in entries['evc-map'].items():
                            if isinstance(item, tuple) and item[0] == 'name':
                                names.add(item[1])
                                break

                    if len(names) > 0:
                        del_xml = '<evc-maps xmlns="http://www.adtran.com/ns/yang/adtran-evc-maps"' + \
                                 ' xc:operation = "delete">'
                        for name in names:
                            del_xml += '<evc-map>'
                            del_xml += '<name>{}</name>'.format(name)
                            del_xml += '</evc-map>'
                        del_xml += '</evc-maps>'
                        log.debug('removing', xml=del_xml)

                        return client.edit_config(del_xml, lock_timeout=30)

            return succeed('no entries')

        d = client.get(get_xml)
        d.addCallbacks(do_delete, request_failed, callbackArgs=[regex_], errbackArgs=['get'])
        d.addCallbacks(delete_complete, request_failed, errbackArgs=['edit-config'])
        return d
