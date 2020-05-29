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

import functools
import os
import subprocess
import yaml

import traceback

KUBEADMIN = '/etc/kubernetes/admin.conf'
TAINT_EXCEPTION = 'taint "node-role.kubernetes.io/master" not found'


def add_kubeconfig_in_environ(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        os.environ['KUBECONFIG'] = KUBEADMIN
        return func(*args, **kwargs)
    return wrapper


class KubeWorker(object):

    def __init__(self, params):
        self.params = params
        self.module_name = self.params.get('module_name')
        self.module_args = self.params.get('module_args')
        self.is_ha = self.params.get('is_ha')
        self.kube_api = self.params.get('kube_api')
        self.changed = False
        # Use this to store arguments to pass to exit_json()
        self.result = {}

    def _run(self, cmd):
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                shell=True)
        stdout, stderr = proc.communicate()
        retcode = proc.poll()
        if retcode != 0:
            # NOTE(caoyingjun): handler kubectl taint comand especially,
            # since it not idempotent.
            if retcode == 1 and TAINT_EXCEPTION in stderr:
                return stdout
            output = 'stdout: "%s", stderr: "%s"' % (stdout, stderr)
            raise subprocess.CalledProcessError(retcode, cmd, output)
        return stdout

    @property
    def _is_kube_cluster_exists(self):
        if not os.path.exists(KUBEADMIN):
            return False

        # Export KUBECONFIG into environ
        os.environ['KUBECONFIG'] = KUBEADMIN

        cmd = 'kubectl cluster-info'
        kube_result = self._run(cmd)
        if 'is running at' in kube_result:
            return True
        return False

    @property
    def is_bootstrap(self):
        if (self.module_name == 'kubeadm'
           and self.module_args.startswith('init')):
            return True
        return False

    @property
    def is_node_add(self):
        if (self.module_name == 'kubeadm'
           and self.module_args.startswith('join')):
            return True
        return False

    @property
    def is_kubectl(self):
        if self.module_name == 'kubectl':
            return True
        return False

    @property
    def commandlines(self):
        cmd = []
        cmd.append(self.module_name)
        cmd.append(self.module_args)
        if self.is_ha:
            control_cmd = ('--control-plane-endpoint {kube_api} '
                           '--upload-certs'.format(kube_api=self.kube_api))
            cmd.append(control_cmd)

        if self.params.get('module_extra_vars'):
            module_extra_vars = self.params.get('module_extra_vars')
            if isinstance(module_extra_vars, dict):
                if self.is_bootstrap:
                    module_extra_vars = ' '.join('--{}={}'.format(key, value)  # noqa
                                        for key, value in module_extra_vars.items() if value)  # noqa
                if self.is_node_add:
                    extra_cmd = ''
                    for key, value in module_extra_vars.items():
                        if key == 'discovery-token-ca-cert-hash':
                            extra_cmd += ' '.join(
                                ['--' + key, 'sha256:' + value])
                        else:
                            extra_cmd += ' '.join(['--' + key, value])
                        extra_cmd += ' '

                    module_extra_vars = extra_cmd[:-1]

                cmd.append(module_extra_vars)

        return ' '.join(cmd)

    @add_kubeconfig_in_environ
    def get_token(self):
        cmd = 'kubeadm token list'
        tokens = self._run(cmd)

        if 'system:bootstrappers' not in tokens:
            tokens = []
        else:
            tokens = tokens.split('\n')[1:]

        for tk in tokens:
            if not tk:
                continue
            tk = tk.split()
            if int(tk[1][:-1]) > 0:
                token = tk[0]
                break
        else:
            # if all the token are inactive, recreate it.
            recmd = 'kubeadm token create'
            new_token = self._run(recmd)
            token = new_token[:-1]
            self.changed = True

        self.result['token'] = token

    # Get he apiserver from KUBECONFIG
    def get_kube_apiserver(self):
        with open(KUBEADMIN, 'r') as f:
            kubeconfig = yaml.load(f)

        kube_apiserver = kubeconfig['clusters'][0]['cluster']['server']

        self.result['apiserver'] = kube_apiserver.split('//')[-1]

    def get_token_ca_cert_hash(self):
        cmd = ("openssl x509 -pubkey -in /etc/kubernetes/pki/ca.crt | "
               "openssl rsa -pubin -outform der 2>/dev/null | "
               "openssl dgst -sha256 -hex | sed 's/^.* //'")
        token_ca_cert_hash = self._run(cmd)

        self.result['token_ca_cert_hash'] = token_ca_cert_hash[:-1]

    @add_kubeconfig_in_environ
    def get_certificate_key(self):
        if (self.is_ha and
            (self.result['update_nodes']['docker-master'] or
             self.result['update_nodes']['containerd-master'])):
            cmd = 'kubeadm init phase upload-certs --upload-certs'
            certificate_key = self._run(cmd)
            certificate_key = certificate_key.split()[-1]
            self.result['certificate_key'] = certificate_key
        else:
            self.result['certificate_key'] = None

    @property
    @add_kubeconfig_in_environ
    def kube_nodes(self):
        cmd = 'kubectl get node -o wide'
        # To strip the lastest '\n'
        nodes = self._run(cmd).strip()
        return nodes.split('\n')[1:]

    @property
    def nodes_by_runtime(self):
        node_map = {
            'docker': [],
            'containerd': []
        }
        for node in self.kube_nodes:
            node_item = node.split()
            if node_item[-1].startswith('docker://'):
                node_map['docker'].append(node_item[0])
            if node_item[-1].startswith('containerd://'):
                node_map['containerd'].append(node_item[0])
        return node_map

    def get_update_nodes(self):
        # Get the nodes which need to add by runtime
        kube_groups = self.params.get('kube_groups')

        self.result['update_nodes'] = {
            'docker-master': list(set(kube_groups['docker_master']) - set(self.nodes_by_runtime['docker'])),
            'containerd-master': list(set(kube_groups['containerd_master']) - set(self.nodes_by_runtime['containerd'])),
            'docker-node': list(set(kube_groups['docker_node']) - set(self.nodes_by_runtime['docker'])),
            'containerd-node': list(set(kube_groups['containerd_node']) - set(self.nodes_by_runtime['containerd']))
        }

    def run(self):
        if self.is_bootstrap:
            if not self._is_kube_cluster_exists:
                bootstrap_result = self._run(self.commandlines)
                self.changed = True
                self.result['bootstrap_result'] = bootstrap_result
        else:
            if self.is_kubectl:
                # Export KUBECONFIG into environ
                os.environ['KUBECONFIG'] = KUBEADMIN
            kube_result = self._run(self.commandlines)

            # For idempotence, when is kubectl apply, the changed is always
            # False.
            if not self.module_args.startswith('apply'):
                self.changed = True
            self.result['kube_result'] = kube_result

    def get(self):
        self.get_kube_apiserver()
        self.get_update_nodes()
        self.get_token()
        self.get_token_ca_cert_hash()
        self.get_certificate_key()


def main():
    specs = dict(
        module_name=dict(type='str'),
        module_args=dict(type='str'),
        kube_groups=dict(type='json'),
        kube_action=dict(type='str', default='run'),
        module_extra_vars=dict(type='json'),
        is_ha=dict(type='bool', default=False),
        kube_api=dict(type='str')
    )
    module = AnsibleModule(argument_spec=specs, bypass_checks=True)
    params = module.params

    bw = None
    try:
        bw = KubeWorker(params)
        getattr(bw, params.get('kube_action'))()
        module.exit_json(changed=bw.changed, result=bw.result)
    except Exception:
        module.fail_json(changed=True, msg=repr(traceback.format_exc()),
                         **getattr(bw, 'result', {}))


# import module snippets
from ansible.module_utils.basic import *  # noqa
if __name__ == '__main__':
    main()
