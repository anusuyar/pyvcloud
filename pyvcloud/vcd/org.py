# VMware vCloud Director Python SDK
# Copyright (c) 2014 VMware, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from lxml import etree
from lxml import objectify
import os
from pyvcloud.vcd.client import _TaskMonitor
from pyvcloud.vcd.client import E
from pyvcloud.vcd.client import E_OVF
from pyvcloud.vcd.client import EntityType
from pyvcloud.vcd.client import find_link
from pyvcloud.vcd.client import get_links
from pyvcloud.vcd.client import MissingRecordException
from pyvcloud.vcd.client import QueryResultFormat
from pyvcloud.vcd.client import RelationType
from pyvcloud.vcd.system import System
from pyvcloud.vcd.utils import access_settings_to_dict
from pyvcloud.vcd.utils import get_admin_href
from pyvcloud.vcd.utils import to_dict
import shutil
import tarfile
import tempfile
import time
import traceback

DEFAULT_CHUNK_SIZE = 1024 * 1024


class Org(object):
    def __init__(self, client, href=None, resource=None):
        """
        Constructor for Org objects.

        :param client: (pyvcloud.vcd.client): The client.
        :param href: (str): URI of the entity.
        :param resource: (lxml.objectify.ObjectifiedElement): XML representation of the entity.

        """  # NOQA
        self.client = client
        self.href = href
        self.resource = resource
        if resource is not None:
            self.href = resource.get('href')
        self.href_admin = self.href.replace('/api/org/', '/api/admin/org/')

    def reload(self):
        self.resource = self.client.get_resource(self.href)

    def get_name(self):
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        return self.resource.get('name')

    def create_catalog(self, name, description):
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        catalog = E.AdminCatalog(E.Description(description), name=name)
        return self.client.post_linked_resource(
            self.resource, RelationType.ADD, EntityType.ADMIN_CATALOG.value,
            catalog)

    def delete_catalog(self, name):
        org = self.client.get_resource(self.href)
        links = get_links(
            org, rel=RelationType.DOWN, media_type=EntityType.CATALOG.value)
        for link in links:
            if name == link.name:
                admin_href = link.href.replace('/api/catalog/',
                                               '/api/admin/catalog/')
                return self.client.delete_resource(admin_href)
        raise Exception('Catalog not found.')

    def list_catalogs(self):
        if self.client.is_sysadmin():
            resource_type = 'adminCatalog'
        else:
            resource_type = 'catalog'
        result = []
        q = self.client.get_typed_query(
            resource_type, query_result_format=QueryResultFormat.ID_RECORDS)
        records = list(q.execute())
        if len(records) > 0:
            for r in records:
                result.append(
                    to_dict(
                        r,
                        resource_type=resource_type,
                        exclude=['owner', 'org']))
        return result

    def get_catalog(self, name):
        return self.get_catalog_resource(name, False)

    def get_catalog_resource(self, name, is_admin_operation=False):
        org = self.client.get_resource(self.href)
        links = get_links(
            org, rel=RelationType.DOWN, media_type=EntityType.CATALOG.value)
        for link in links:
            if name == link.name:
                href = link.href
                if is_admin_operation:
                    href = href.replace('/api/catalog/', '/api/admin/catalog/')
                return self.client.get_resource(href)
        raise Exception('Catalog not found (or)'
                        ' Access to resource is forbidden')

    def update_catalog(self, old_catalog_name, new_catalog_name, description):
        """
        Update the name and/or description of a catalog.
        :param old_catalog_name: (str): The current name of the catalog.
        :param new_catalog_name: (str): The new name of the catalog.
        :param description: (str): The new description of the catalog.
        :return:  A :class:`lxml.objectify.StringElement` object describing
        the updated catalog.
        """  # NOQA
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        org = self.resource
        links = get_links(
            org, rel=RelationType.DOWN, media_type=EntityType.CATALOG.value)
        for link in links:
            if old_catalog_name == link.name:
                catalog = self.client.get_resource(link.href)
                href = catalog.get('href')
                admin_href = href.replace('/api/catalog/',
                                          '/api/admin/catalog/')
                admin_view_of_catalog = self.client.get_resource(admin_href)
                if new_catalog_name is not None:
                    admin_view_of_catalog.set('name', new_catalog_name)
                if description is not None:
                    admin_view_of_catalog['Description'] = E.Description(
                        description)
                return self.client.put_resource(
                    admin_href,
                    admin_view_of_catalog,
                    media_type=EntityType.ADMIN_CATALOG.value)
        raise Exception('Catalog not found.')

    def share_catalog(self, name, share=True):
        catalog = self.get_catalog(name)
        is_published = 'true' if share else 'false'
        params = E.PublishCatalogParams(E.IsPublished(is_published))
        href = catalog.get('href') + '/action/publish'
        admin_href = href.replace('/api/catalog/', '/api/admin/catalog/')
        return self.client.post_resource(
            admin_href,
            params,
            media_type=EntityType.PUBLISH_CATALOG_PARAMS.value)

    def list_catalog_items(self, name):
        catalog = self.get_catalog(name)
        items = []
        for i in catalog.CatalogItems.getchildren():
            items.append({'name': i.get('name'), 'id': i.get('id')})
        return items

    def get_catalog_item(self, name, item_name):
        catalog = self.get_catalog(name)
        for i in catalog.CatalogItems.getchildren():
            if i.get('name') == item_name:
                return self.client.get_resource(i.get('href'))
        raise Exception('Catalog item not found.')

    def delete_catalog_item(self, name, item_name):
        catalog = self.get_catalog(name)
        for i in catalog.CatalogItems.getchildren():
            if i.get('name') == item_name:
                return self.client.delete_resource(i.get('href'))
        raise Exception('Item not found.')

    def upload_media(self,
                     catalog_name,
                     file_name,
                     item_name=None,
                     description='',
                     chunk_size=DEFAULT_CHUNK_SIZE,
                     callback=None):
        stat_info = os.stat(file_name)
        catalog = self.get_catalog(catalog_name)
        if item_name is None:
            item_name = os.path.basename(file_name)
        image_type = os.path.splitext(item_name)[1][1:]
        media = E.Media(
            name=item_name, size=str(stat_info.st_size), imageType=image_type)
        media.append(E.Description(description))
        catalog_item = self.client.post_resource(
            catalog.get('href') + '/action/upload', media,
            EntityType.MEDIA.value)
        entity = self.client.get_resource(catalog_item.Entity.get('href'))
        file_href = entity.Files.File.Link.get('href')
        return self.upload_file(
            file_name, file_href, chunk_size=chunk_size, callback=callback)

    def download_catalog_item(self,
                              catalog_name,
                              item_name,
                              file_name,
                              chunk_size=DEFAULT_CHUNK_SIZE,
                              callback=None,
                              task_callback=None):
        item = self.get_catalog_item(catalog_name, item_name)
        item_type = item.Entity.get('type')
        enable_href = item.Entity.get('href') + '/action/enableDownload'
        task = self.client.post_resource(enable_href, None, None)
        tm = _TaskMonitor(self.client)
        tm.wait_for_success(task, 60, 1, callback=task_callback)
        item = self.client.get_resource(item.Entity.get('href'))
        bytes_written = 0
        if item_type == EntityType.MEDIA.value:
            size = item.Files.File.get('size')
            download_href = item.Files.File.Link.get('href')
            bytes_written = self.client.download_from_uri(
                download_href,
                file_name,
                chunk_size=chunk_size,
                size=size,
                callback=callback)
        elif item_type == EntityType.VAPP_TEMPLATE.value:
            ovf_descriptor = self.client.get_linked_resource(
                item, RelationType.DOWNLOAD_DEFAULT, EntityType.TEXT_XML.value)
            transfer_uri = find_link(item, RelationType.DOWNLOAD_DEFAULT,
                                     EntityType.TEXT_XML.value).href
            transfer_uri = transfer_uri.replace('/descriptor.ovf', '/')
            tempdir = None
            cwd = os.getcwd()
            try:
                tempdir = tempfile.mkdtemp(dir='.')
                ovf_file = os.path.join(tempdir, 'descriptor.ovf')
                with open(ovf_file, 'wb') as f:
                    payload = etree.tostring(
                        ovf_descriptor,
                        pretty_print=True,
                        xml_declaration=True,
                        encoding='utf-8')
                    f.write(payload)

                ns = '{http://schemas.dmtf.org/ovf/envelope/1}'
                files = []
                for f in ovf_descriptor.References.File:
                    source_file = {
                        'href': f.get(ns + 'href'),
                        'name': f.get(ns + 'id'),
                        'size': f.get(ns + 'size')
                    }
                    target_file = os.path.join(tempdir, source_file['href'])
                    uri = transfer_uri + source_file['href']
                    num_bytes = self.client.download_from_uri(
                        uri,
                        target_file,
                        chunk_size=chunk_size,
                        size=source_file['size'],
                        callback=callback)
                    if num_bytes != source_file['size']:
                        raise Exception('download incomplete for file %s' %
                                        source_file['href'])
                    files.append(source_file)
                with tarfile.open(file_name, 'w') as tar:
                    os.chdir(tempdir)
                    tar.add('descriptor.ovf')
                    for f in files:
                        tar.add(f['href'])
            finally:
                if tempdir is not None:
                    os.chdir(cwd)
                    stat_info = os.stat(file_name)
                    bytes_written = stat_info.st_size
                    # shutil.rmtree(tempdir)
        return bytes_written

    def upload_file(self,
                    file_name,
                    href,
                    chunk_size=DEFAULT_CHUNK_SIZE,
                    callback=None):
        transferred = 0
        stat_info = os.stat(file_name)
        with open(file_name, 'rb') as f:
            while transferred < stat_info.st_size:
                my_bytes = f.read(chunk_size)
                if len(my_bytes) <= chunk_size:
                    range_str = 'bytes %s-%s/%s' % \
                                (transferred,
                                 len(my_bytes)-1,
                                 stat_info.st_size)
                    self.client.upload_fragment(href, my_bytes, range_str)
                    transferred += len(my_bytes)
                    if callback is not None:
                        callback(transferred, stat_info.st_size)
        return transferred

    def upload_ovf(self,
                   catalog_name,
                   file_name,
                   item_name=None,
                   description='',
                   chunk_size=DEFAULT_CHUNK_SIZE,
                   callback=None):
        catalog = self.get_catalog(catalog_name)
        if item_name is None:
            item_name = os.path.basename(file_name)
        tempdir = tempfile.mkdtemp(dir='.')
        total_bytes = 0
        try:
            ova = tarfile.open(file_name)
            ova.extractall(path=tempdir)
            ova.close()
            ovf_file = None
            files = os.listdir(tempdir)
            for f in files:
                fn, ex = os.path.splitext(f)
                if ex == '.ovf':
                    ovf_file = os.path.join(tempdir, f)
                    break
            if ovf_file is not None:
                stat_info = os.stat(ovf_file)
                total_bytes += stat_info.st_size
                ovf = objectify.parse(ovf_file)
                files = []
                ns = '{http://schemas.dmtf.org/ovf/envelope/1}'
                for f in ovf.getroot().References.File:
                    source_file = {
                        'href': f.get(ns + 'href'),
                        'name': f.get(ns + 'id'),
                        'size': f.get(ns + 'size')
                    }
                    files.append(source_file)
                if item_name is None:
                    item_name = os.path.basename(file_name)
                params = E.UploadVAppTemplateParams(name=item_name)
                params.append(E.Description(description))
                catalog_item = self.client.post_resource(
                    catalog.get('href') + '/action/upload', params,
                    EntityType.UPLOAD_VAPP_TEMPLATE_PARAMS.value)
                entity = self.client.get_resource(
                    catalog_item.Entity.get('href'))
                file_href = entity.Files.File.Link.get('href')
                self.client.put_resource(file_href, ovf, 'text/xml')
                while True:
                    time.sleep(5)
                    entity = self.client.get_resource(
                        catalog_item.Entity.get('href'))
                    if len(entity.Files.File) > 1:
                        break
                for source_file in files:
                    for target_file in entity.Files.File:
                        if source_file.get('href') == target_file.get('name'):
                            file_path = os.path.join(tempdir,
                                                     source_file.get('href'))
                            total_bytes += self.upload_file(
                                file_path,
                                target_file.Link.get('href'),
                                chunk_size=chunk_size,
                                callback=callback)
            shutil.rmtree(tempdir)
        except Exception as e:
            print(traceback.format_exc())
            shutil.rmtree(tempdir)
            raise e
        return total_bytes

    def get_vdc(self, name):
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        links = get_links(
            self.resource,
            rel=RelationType.DOWN,
            media_type=EntityType.VDC.value)
        for link in links:
            if name == link.name:
                return self.client.get_resource(link.href)

    def list_vdcs(self):
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        result = []
        for v in get_links(self.resource, media_type=EntityType.VDC.value):
            result.append({'name': v.name, 'href': v.href})
        return result

    def capture_vapp(self,
                     catalog_resource,
                     vapp_href,
                     catalog_item_name,
                     description,
                     customize_on_instantiate=False):
        contents = E.CaptureVAppParams(
            E.Description(description),
            E.Source(href=vapp_href),
            name=catalog_item_name)
        if customize_on_instantiate:
            contents.append(
                E.CustomizationSection(
                    E_OVF.Info('VApp template customization section'),
                    E.CustomizeOnInstantiate('true')))
        return self.client.post_linked_resource(
            catalog_resource,
            rel=RelationType.ADD,
            media_type=EntityType.CAPTURE_VAPP_PARAMS.value,
            contents=contents)

    def create_user(self,
                    user_name,
                    password,
                    role_href,
                    full_name='',
                    description='',
                    email='',
                    telephone='',
                    im='',
                    alert_email='',
                    alert_email_prefix='',
                    stored_vm_quota=0,
                    deployed_vm_quota=0,
                    is_group_role=False,
                    is_default_cached=False,
                    is_external=False,
                    is_alert_enabled=False,
                    is_enabled=False):
        """
        Create User in the current Org
        :param user_name: The username of the user
        :param password: The password of the user
        :param role_href: The href of the user role
        :param full_name: The full name of the user
        :param description: The description for the User
        :param email: The email of the user
        :param telephone: The telephone of the user
        :param im: The im address of the user
        :param alert_email: The alert email address
        :param alert_email_prefix: The string to prepend to the alert message
                subject line
        :param stored_vm_quota: The quota of vApps that this user can store
        :param deployed_vm_quota: The quota of vApps that this user can deploy
                concurrently
        :param is_group_role: Indicates if the user has a group role
        :param is_default_cached: Indicates if user should be cached
        :param is_external: Indicates if user is imported from an external
                source
        :param is_alert_enabled: The alert email address
        :param is_enabled: Enable user
        :return: (UserType) Created user object
        """  # NOQA
        resource_admin = self.client.get_resource(self.href_admin)
        user = E.User(
            E.Description(description),
            E.FullName(full_name),
            E.EmailAddress(email),
            E.Telephone(telephone),
            E.IsEnabled(is_enabled),
            E.IM(im),
            E.IsAlertEnabled(is_alert_enabled),
            E.AlertEmailPrefix(alert_email_prefix),
            E.AlertEmail(alert_email),
            E.IsExternal(is_external),
            E.IsDefaultCached(is_default_cached),
            E.IsGroupRole(is_group_role),
            E.StoredVmQuota(stored_vm_quota),
            E.DeployedVmQuota(deployed_vm_quota),
            E.Role(href=role_href),
            E.Password(password),
            name=user_name)
        return self.client.post_linked_resource(
            resource_admin, RelationType.ADD, EntityType.USER.value, user)

    def update_user(self, user_name, is_enabled=None):
        """
        Update an User
        :param user_name: (str): username of the user
        :param is_enabled: (bool): enable/disable the user
        :return: (UserType) Updated user object
        """  # NOQA
        user = self.get_user(user_name)
        if is_enabled is not None:
            if hasattr(user, 'IsEnabled'):
                user['IsEnabled'] = E.IsEnabled(is_enabled)
                return self.client.put_resource(
                    user.get('href'), user, EntityType.USER.value)
        return user

    def get_user(self, user_name):
        """
        Retrieve user record from current Organization
        :param user_name: user name of the record to be retrieved
        :return: User record
        """  # NOQA
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        resource_type = 'user'
        org_filter = None
        if self.client.is_sysadmin():
            resource_type = 'adminUser'
            org_filter = 'org==%s' % self.resource.get('href')
        query = self.client.get_typed_query(
            resource_type,
            query_result_format=QueryResultFormat.REFERENCES,
            equality_filter=('name', user_name),
            qfilter=org_filter)
        records = list(query.execute())
        if len(records) == 0:
            raise Exception('user not found')
        elif len(records) > 1:
            raise Exception('multiple users found')
        return self.client.get_resource(records[0].get('href'))

    def delete_user(self, user_name):
        """
        Delete user record from current organization
        :param user_name: (str) name of the user that (org/sys)admins wants to delete
        :return: result of calling DELETE on the user resource
        """  # NOQA
        user = self.get_user(user_name)
        return self.client.delete_resource(user.get('href'))

    def list_roles(self):
        """
        Retrieve the list of role in the current Org
        :return: List of roles in the current Org
        """  # NOQA
        roles_query, resource_type = self.get_roles_query()
        result = []
        for r in list(roles_query.execute()):
            result.append(
                to_dict(
                    r,
                    resource_type=resource_type,
                    exclude=['org', 'orgName', 'href']))
        return result

    def get_role(self, role_name):
        """
        Retrieve role object with a particular name in the current Org
        :param role_name: (str): The name of the role object to be retrieved
        :return: (QueryResultRoleRecordType): Role query result in records
                 format
        """  # NOQA
        try:
            roles_query = self.get_roles_query(('name', role_name))[0]
            return roles_query.find_unique()
        except MissingRecordException:
            raise Exception('Role \'%s\' does not exist.' % role_name)

    def get_roles_query(self, name_filter=None):
        """
        Get the typed query for the roles in the current Org
        :param name_filter: (tuple): (name ,'role name') Filter the roles by
                             'role name'
        :return: (tuple of (_TypedQuery, str))
                  _TypedQuery object represents the query for the roles in
                  the current Org
                  str represents the resource type of the query object
        """  # NOQA
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)

        org_filter = None
        resource_type = 'role'
        if self.client.is_sysadmin():
            resource_type = 'adminRole'
            org_filter = 'org==%s' % self.resource.get('href')

        query = self.client.get_typed_query(
            resource_type,
            query_result_format=QueryResultFormat.RECORDS,
            equality_filter=name_filter,
            qfilter=org_filter)
        return query, resource_type

    def get_catalog_access_control_settings(self, catalog_name):
        """
        Get the access control settings of a catalog.
        :param catalog_name: (str): The name of the catalog.
        :return: Access control settings of the catalog.
        """  # NOQA
        catalog_resource = self.get_catalog(name=catalog_name)
        control_access = self.client.get_linked_resource(
            catalog_resource, RelationType.DOWN,
            EntityType.CONTROL_ACCESS_PARAMS.value)
        access_settings = []
        if hasattr(control_access, 'AccessSettings') and \
                hasattr(control_access.AccessSettings, 'AccessSetting') and \
                len(control_access.AccessSettings.AccessSetting) > 0:
            for access_setting in list(
                    control_access.AccessSettings.AccessSetting):
                access_settings.append(access_settings_to_dict(access_setting))
        result = to_dict(control_access)
        if len(access_settings) > 0:
            result['AccessSettings'] = access_settings
        return result

    def change_catalog_owner(self, catalog_name, user_name):
        """
        Change the ownership of Catalog to a given user
        :param catalog_name: Catalog whose ownership needs to be changed
        :param user_name: New Owner of the Catalog
        :return: None
        """  # NOQA
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        catalog_resource = self.get_catalog_resource(
            catalog_name, is_admin_operation=True)
        owner_link = find_link(
            catalog_resource,
            rel=RelationType.DOWN,
            media_type=EntityType.OWNER.value,
            fail_if_absent=True)
        catalog_href = owner_link.href

        user_resource = self.get_user(user_name)
        new_owner = catalog_resource.Owner
        new_owner.User.set('href', user_resource.get('href'))
        objectify.deannotate(new_owner)

        return self.client.put_resource(catalog_href, new_owner,
                                        EntityType.OWNER.value)

    def update_org(self, org_name, is_enabled=None):
        """
        Update an organization
        :param org_name: (str): The name of the organization.
        :param is_enabled: (bool): enable/disable the organization
        :return: (AdminOrgType) updated org object.
        """  # NOQA
        org = self.client.get_org_by_name(org_name)
        org_admin_href = get_admin_href(org.get('href'))
        org_admin_resource = self.client.get_resource(org_admin_href)
        if is_enabled is not None:
            if hasattr(org_admin_resource, 'IsEnabled'):
                org_admin_resource['IsEnabled'] = E.IsEnabled(is_enabled)
                return self.client.put_resource(org_admin_href,
                                                org_admin_resource,
                                                EntityType.ADMIN_ORG.value)
        return org_admin_resource

    def create_org_vdc(self,
                       vdc_name,
                       provider_vdc_name,
                       description='',
                       allocation_model='AllocationVApp',
                       cpu_units='MHz',
                       cpu_allocated=0,
                       cpu_limit=0,
                       mem_units='MB',
                       mem_allocated=0,
                       mem_limit=0,
                       nic_quota=0,
                       network_quota=0,
                       vm_quota=0,
                       storage_profiles=[],
                       resource_guaranteed_memory=0.0,
                       resource_guaranteed_cpu=0.0,
                       vcpu_in_mhz=1000,
                       is_thin_provision=True,
                       network_pool_name=None,
                       uses_fast_provisioning=True,
                       over_commit_allowed=True,
                       vm_discovery_enabled=None,
                       is_enabled=True):
        """
        Create Organization VDC in the current Org.
        :param vdc_name: The name of the new org vdc.
        :param provider_vdc_name: The name of the new provider vdc.
        :param description: The description of the new org vdc.
        :param allocation_model: The allocation model used by this vDC. One of AllocationVApp, AllocationPool or ReservationPool.
        :param cpu_units: The cpu units compute capacity allocated to this vDC. One of MHz or GHz
        :param cpu_allocated: Capacity that is committed to be available.
        :param cpu_limit: Capacity limit relative to the value specified for Allocation.
        :param mem_units: The memory units compute capacity allocated to this vDC. One of MB or GB.
        :param mem_allocated: Memory capacity that is committed to be available.
        :param mem_limit: Memory capacity limit relative to the value specified for Allocation.
        :param nic_quota: Maximum number of virtual NICs allowed in this vDC. Defaults to 0, which specifies an unlimited number.
        :param network_quota: Maximum number of network objects that can be deployed in this vDC. Defaults to 0, which means no networks can be deployed.
        :param vm_quota: The maximum number of VMs that can be created in this vDC. Defaults to 0, which specifies an unlimited number.
        :param storage_profiles: List of provider vDC storage profiles to add to this vDC.
            Each item is a dictionary that should include the following elements:
                name: (string) name of the PVDC storage profile.
                enabled: (bool) True if the storage profile is enabled for this vDC.
                units: (string) Units used to define limit. One of MB or GB.
                limit: (int) Max number of units allocated for this storage profile.
                default: (bool) True if this is default storage profile for this vDC.
                       resource_guaranteed_memory=0.0,
                       resource_guaranteed_cpu=0.0,
                       vcpu_in_mhz=1000,
                       is_thin_provision=True,
                       network_pool_name=None,
        :param uses_fast_provisioning: Boolean to request fast provisioning.
       uses_fast_provisioning=True,
                       over_commit_allowed=True,
                       vm_discovery_enabled=None,
        :param is_enabled: True if this vDC is enabled for use by the organization vDCs.
        :return:  A :class:`lxml.objectify.StringElement` object describing the new VDC.
        """  # NOQA
        if self.resource is None:
            self.resource = self.client.get_resource(self.href)
        sys_admin_resource = self.client.get_admin()
        system = System(self.client, admin_resource=sys_admin_resource)
        pvdc = system.get_provider_vdc(provider_vdc_name)
        resource_admin = self.client.get_resource(self.href_admin)
        params = E.CreateVdcParams(
            E.Description(description),
            E.AllocationModel(allocation_model),
            E.ComputeCapacity(
                E.Cpu(
                    E.Units(cpu_units), E.Allocated(cpu_allocated),
                    E.Limit(cpu_limit)),
                E.Memory(
                    E.Units(mem_units), E.Allocated(mem_allocated),
                    E.Limit(mem_limit))),
            E.NicQuota(nic_quota),
            E.NetworkQuota(network_quota),
            E.VmQuota(vm_quota),
            E.IsEnabled(is_enabled),
            name=vdc_name)
        for sp in storage_profiles:
            pvdc_sp = system.get_provider_vdc_storage_profile(sp['name'])
            params.append(
                E.VdcStorageProfile(
                    E.Enabled(sp['enabled']),
                    E.Units(sp['units']),
                    E.Limit(sp['limit']),
                    E.Default(sp['default']),
                    E.ProviderVdcStorageProfile(href=pvdc_sp.get('href'))))
        if network_pool_name is not None:
            npr = system.get_network_pool_reference(network_pool_name)
            href = npr.get('href')
            params.append(
                E.NetworkPoolReference(
                    href=href,
                    id=href.split('/')[-1],
                    type=npr.get('type'),
                    name=npr.get('name')))
        params.append(pvdc)
        params.append(E.UsesFastProvisioning(uses_fast_provisioning))
        if vm_discovery_enabled is not None:
            params.append(E.VmDiscoveryEnabled(vm_discovery_enabled))
        return self.client.post_linked_resource(
            resource_admin, RelationType.ADD, EntityType.VDCS_PARAMS.value,
            params)

    def delete_org_vdc(self, name):
        """
        Delete Organization VDC in the current Org
        :param vdc_name: The name of the org vdc to delete
        :return:
        """  # NOQA
        from pyvcloud.vcd.vdc import VDC
        vdc_resource = self.get_vdc(name)
        vdc = VDC(self.client, resource=vdc_resource)
        return self.client.delete_linked_resource(vdc.resource,
                                                  RelationType.REMOVE, None)
