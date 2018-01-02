#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Vincent<vincent8280@outlook.com>
#         http://blog.vincentzhong.cn
# Created on 2017/4/11 18:14
import os
from copy import deepcopy
from flask import render_template, Response, Flask, request

from web2kindle import load_config
from web2kindle.libs.utils import write_config
from web2kindle.script import SCRIPTS, SCRIPT_CONFIGS, SCRIPT_FUNC

app = Flask(__name__)
app.debug = True


@app.route('/')
def index_page():
    return render_template('index.html', scripts=SCRIPTS)


@app.route('/config', methods=['POST', 'GET'])
def config_page():
    config_path = './web2kindle/config'
    configs = deepcopy(SCRIPT_CONFIGS)

    if request.method == 'GET':
        # 加载默认值
        for each_script in configs:
            path = os.path.join(config_path, each_script['script_name'] + '.yml')
            a = load_config(path)
            for config_name, config_value in a.items():
                for each_config in each_script['configs']:
                    if each_config['config_name'] == config_name:
                        each_config['value'] = config_value
        return render_template('config.html', configs=configs)
    elif request.method == 'POST':
        new_config = {}

        form_data = request.form.to_dict()
        for k, v in form_data.items():
            if '_check' in k:
                new_config[k.replace('_check', '')] = form_data[k.replace('_check', '')]

        write_config(os.path.join(config_path, form_data['script_name'] + '.yml'), new_config)
        return Response()


@app.route('/doc')
def doc_page():
    return render_template('doc.html')


@app.route('/guide')
def guide_page():
    return render_template('guide.html')


@app.route('/action', methods=['POST'])
def action():
    form_data = request.form.to_dict()
    script_name = form_data['script_name']
    form_data.pop('script_name')
    form_data.setdefault('img', False)
    form_data.setdefault('gif', False)
    form_data.setdefault('email', False)

    kw = {}
    for k, v in form_data.items():
        if k == 'img' or k == 'gif' or k == 'email':
            if v is not False:
                v = True
        if v != '':
            kw.setdefault(k, v)

    SCRIPT_FUNC[script_name](**kw)
    return Response()


if __name__ == '__main__':
    app.run()
