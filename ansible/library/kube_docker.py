#!/usr/bin/env python
#
# Copyright 2019 Caoyingjun
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

DOCUMENTATION = '''
author: Caoyingjun

'''

import subprocess
import traceback

master_images = ['kube-apiserver',
                 'kube-controller-manager',
                 'kube-scheduler',
                 'etcd',
                 'coredns',
                 'kube-proxy',
                 'pause']
worker_images = ['coredns', 'kube-proxy', 'pause']


class DockerWorker(object):

    def __init__(self, module):
        self.module = module
        self.params = self.module.params
        self.changed = False
        self.result = {}
        self.kube_image = self.params.get('kube_image')
        self.kube_repo = self.params.get('kube_repo')
        self.kube_version = self.params.get('kube_version')

    def _run(self, cmd):
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                shell=True)
        stdout, stderr = proc.communicate()
        retcode = proc.poll()
        if retcode != 0:
            output = 'stdout: "%s", stderr: "%s"' % (stdout, stderr)
            raise subprocess.CalledProcessError(retcode, cmd, output)
        return stdout.rstrip()

    def _get_local_images(self):
        images = self._run('docker images')
        return images.split('\n')[1:]

    def get_local_image_id(self, image):
        image_sp = image.split(':')
        image_repo = ':'.join(image_sp[:-1])
        image_tag = image_sp[-1]
        image_entry = self._run(
            'docker images|grep {repo}|grep {tag}'.format(repo=image_repo,
                                                          tag=image_tag))
        if image_entry:
            return image_entry.split()[2]

    @property
    def local_images(self):
        # all images that presents on the machine
        return [':'.join(image.split()[:2])
                for image in self._get_local_images()]

    def pull_image(self):
        # pull the images kubernetes core components needed
        if self.kube_image not in self.local_images:
            image_name = self.kube_image.split('/')[1]

            # NOTE(caoyingjun): Pull the images from ali or private repo.
            ali_image = '/'.join([self.kube_repo, image_name])
            self._run('docker pull {image}'.format(image=ali_image))
            image_id = self.get_local_image_id(ali_image)
            self._run(
                'docker tag {image_id} {kube_image}'.format(image_id=image_id,
                                                            kube_image=self.kube_image))
            if self.params.get('cleanup'):
                self._run('docker rmi {image} -f'.format(image=ali_image))
            self.changed = True

    def get_image(self):
        # Get the images which kubernetes need for seting up cluster
        cmd = ('kubeadm config images list '
               '--kubernetes-version {kube_version}'.format(
                   kube_version=self.kube_version))
        stdout = self._run(cmd)

        images_list = []
        for image in stdout.split():
            image_repo, image_tag = image.split(':')
            image_name = image_repo.split('/')[-1]
            if image_name in master_images:
                images_list.append({'image_repo': image_repo,
                                    'image_tag': image_tag,
                                    'group': 'kube-master'})
            if image_name in worker_images:
                images_list.append({'image_repo': image_repo,
                                    'image_tag': image_tag,
                                    'group': 'kube-worker'})

        self.result['images_list'] = images_list

def main():

    specs = dict(
        kube_image=dict(type='str', default=''),
        kube_repo=dict(type='str', required=True),
        kube_version=dict(type='str', required=True),
        image_action=dict(type='str', default='pull'),
        cleanup=dict(type='bool', default=True),
    )
    module = AnsibleModule(argument_spec=specs, bypass_checks=True)  # noqa

    dw = None
    try:
        dw = DockerWorker(module)
        getattr(dw, '_'.join([module.params.get('image_action'), 'image']))()
        module.exit_json(changed=dw.changed, result=dw.result)
    except Exception:
        module.fail_json(changed=True, msg=repr(traceback.format_exc()),
                         failed=True)


# import module snippets
from ansible.module_utils.basic import *  # noqa
if __name__ == '__main__':
    main()
