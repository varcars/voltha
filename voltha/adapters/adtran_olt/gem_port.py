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

import structlog
import json
from voltha.protos.bbf_fiber_gemport_body_pb2 import GemportsConfigData

log = structlog.get_logger()


class GemPort(object):
    """
    Class to wrap TCont capabilities
    """
    def __init__(self, gem_id, alloc_id,
                 encryption=False,
                 omci_transport=False,
                 multicast=False,
                 tcont_ref=None,
                 ident=None,
                 traffic_class=None,
                 intf_ref=None,
                 exception=False,        # FIXED_ONU
                 name=None):
        self.name = name
        self.gem_id = gem_id
        self._alloc_id = alloc_id
        self.tconf_ref = tcont_ref
        self.intf_ref = intf_ref
        self.traffic_class = traffic_class
        self.id = ident
        self._encryption = encryption
        self._omci_transport = omci_transport
        self.multicast = multicast
        self.exception = exception        # FIXED_ONU

    def __str__(self):
        return "GemPort: {}, alloc-id: {}, gem-id: {}".format(self.name,
                                                              self.alloc_id,
                                                              self.gem_id)

    @staticmethod
    def create(data):
        assert isinstance(data, GemportsConfigData)

        return GemPort(data.gemport_id, None,
                       encryption=data.aes_indicator,
                       tcont_ref=data.tcont_ref,
                       ident=data.id,
                       name=data.name,
                       traffic_class=data.traffic_class,
                       intf_ref=data.itf_ref)            # v_enet

    @property
    def alloc_id(self):
        if self._alloc_id is None:
            #
            # TODO: Resolve this (needs to be OLT handler)
            #
            pass
        return self._alloc_id

    @property
    def encryption(self):
        return self._encryption

    @property
    def omci_transport(self):
        return self._omci_transport

    def to_dict(self):
        return {
            'port-id': self.gem_id,
            'alloc-id': self.alloc_id,
            'encryption': self.encryption,
            'omci-transport': self.omci_transport
        }

    def add_to_hardware(self, session, pon_id, onu_id, operation='POST'):
        from adtran_olt_handler import AdtranOltHandler

        uri = AdtranOltHandler.GPON_GEM_CONFIG_LIST_URI.format(pon_id, onu_id)
        data = json.dumps(self.to_dict())
        name = 'gem-port-create-{}-{}: {}/{}'.format(pon_id, onu_id,
                                                     self.gem_id,
                                                     self.alloc_id)

        return session.request(operation, uri, data=data, name=name)

    def remove_from_hardware(self, session, pon_id, onu_id):
        from adtran_olt_handler import AdtranOltHandler

        uri = AdtranOltHandler.GPON_GEM_CONFIG_URI.format(pon_id, onu_id, self.gem_id)
        name = 'gem-port-delete-{}-{}: {}'.format(pon_id, onu_id, self.gem_id)
        return session.request('DELETE', uri, name=name)
