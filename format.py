#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据格式转换"""
import os
import ast
import random
import difflib

from copy import deepcopy
from functools import partial
from itertools import groupby

from dcg import ERROR, MODE, SYMBOL, STRUC, Template, Engine

# 适用与安全模式的外部实现示例
def _safe(data, *args):
    if not args: return data
    if not data: return ""
    
    for item in args:
        try:
            data = data[item]
        except Exception as e:
            return ""
    
    return data


class Meta(type):
    def __setattr__(cls, name, value):
        super().__setattr__(name, value)
        
        if name == "DEBUG":
            MODE.DEBUG = bool(value)
        elif name == "OPT_MODE":
            MODE.OPT_MODE = bool(value)
        elif name == "FREE_MODE":
            MODE.FREE_MODE = bool(value)
        elif name == "SAFE_MODE":
            MODE.SAFE_MODE = bool(value)
        elif name == "TRYE_MODE":
            MODE.TRYE_MODE = bool(value)
        elif name == "CYTHON_MODE":
            MODE.CYTHON_MODE = bool(value)


class Base():
    @classmethod
    def isConts(cls, content):
        """是否是常量"""
        return True if int(content.get("const", 1)) == 1 else False
    
    @classmethod
    def isVar(cls, describe):
        """内容中是否存在变量"""
        mapping, paramRule, strucRule = {}, [], []
        cls.parseDesc(describe.get("struct", {}), mapping, paramRule, strucRule)
        
        for info in mapping.values():
            if info[0] == SYMBOL.VAR_SYMBOL: return True
        
        return False
    
    @classmethod
    def isExt(cls, describe):
        """内容中是否存在扩展"""
        def __call(data):
            if isinstance(data, dict):
                for _, value in data.items():
                    return __call(value)
            elif isinstance(data, list):
                for item in data:
                    if item.get("ext"): return True
        
        return __call(describe.get("struct", {}))
    
    @classmethod
    def literalEval(cls, value):
        """eval 数据转换"""
        if isinstance(value, int): return value
        try:
            try:
                return ast.literal_eval(value)
            except:
                if value.startswith("lambda "):
                    return eval(value)
                else:
                    return value
        except Exception as e:
            return value
    
    @classmethod
    def updateMap(cls, mapping, infos):
        """更新映射表"""
        for name, val in infos:
            # if name in mapping:
                # print(f"Warning: '{name}' is redefined!")
                # raise VarRedefinition(f"'{name}' is redefined!")
            mapping[name] = val

        return True  
    
    @classmethod
    def cash(cls, rule, mapping):
        if isinstance(rule, dict):
            for key, val in rule.items():
                if isinstance(val, partial):
                    rule[key] = val(mapping)
                else:
                    cls.cash(val, mapping)
        elif isinstance(rule, (list, tuple)):
            for i, val in enumerate(rule):
                if isinstance(val, partial):
                    rule[i] = val(mapping)
                else:
                    cls.cash(val, mapping)
        
        return True
    
    @classmethod
    def descOpt(cls, describe, limit):
        """内容描述优化"""
        def __refOpt(docs):
            """引用优化"""
            infos = []
            for i, doc in enumerate(docs):
                if doc["name"] in limit: continue
            
                for ref in ["value", "ext"]:
                    info = cls.expParse(doc.get(ref), {})
                    if info and info["map"]: 
                        for item in info["map"].values():
                            infos.append((i, item[-1]))
            
            
            infos = sorted(infos, key=lambda x: str(x[-1]))
            length = max([len(x) for _, x in infos]) if infos else 0
            
            temp = {}
            for i in range(2, length):
                for name, glist in groupby(infos, key=lambda x: x[-1][:i]):
                    glist = list(glist)
                    if len(glist) <= 1: continue
                
                    for item in glist:
                        temp.setdefault(item[0], []).append(name)
            
            temp = {idx: sorted(info, key=lambda x: x[0])[-1] for idx, info in temp.items()}
            temp = sorted(temp.items(), key=lambda x: (len(x[-1]), str(x[-1])), reverse=True)
            word = "".join([chr(random.randint(65, 90)) for _ in range(6)])
            
            varInfos = []
            for i, (name, glist) in enumerate(groupby(temp, key=lambda x: x[1]), 1):
                name = [str(x) for x in name]
                varname = f"VAR_{word}_{i}"
                flag = True
                
                for idx, varinfo in glist:
                    info = cls.expParse(docs[idx].get("value"), {})
                    if info:
                        var = list(info["map"].values())[-1]
                        if var[0] in {SYMBOL.XPATH_SYMBOL, SYMBOL.VAR_SYMBOL}:
                            if var[0] == SYMBOL.XPATH_SYMBOL:
                                varvalue = f"{SYMBOL.XPATH_SYMBOL}.{'.'.join(name[1:])}"
                            elif var[0] == SYMBOL.VAR_SYMBOL:
                                varvalue = f"{SYMBOL.VAR_SYMBOL}.{'.'.join(name)}"
                        
                            if flag:
                                varInfos.append({"name": varname, "alias": "", "show": False, "value": varvalue})
                                flag = False
                        
                            docs[idx]["value"] = f"{SYMBOL.VAR_SYMBOL}.{varname}.{docs[idx]['value'][len(varvalue)+1:]}"
                    
                    info = cls.expParse(docs[idx].get("ext"), {})
                    if info:
                        for var in sorted(info["map"], reverse=True):
                            if var[0] == SYMBOL.XPATH_SYMBOL:
                                varvalue = f"{SYMBOL.XPATH_SYMBOL}.{'.'.join(name[1:])}"
                            elif var[0] == SYMBOL.VAR_SYMBOL:
                                varvalue = f"{SYMBOL.VAR_SYMBOL}.{'.'.join(name)}"
                            else:
                                continue
                            
                            if flag:
                                varInfos.append({"name": varname, "alias": "", "show": False, "value": varvalue})
                                flag = False
                            
                            docs[idx]["ext"] = docs[idx]["ext"].replace(var, f"{varname}" + "".join([f"[{repr(x)}]" for x in info["map"][var][-1][len(name):]]))
            
            for i in varInfos:
                docs.insert(0, i)
                
        def __invOpt(docs):
            """无效定义优化"""
            def _find(data, buffer=None):
                buffer = {} if buffer == None else buffer
                
                if isinstance(data, (list, tuple)):
                    for doc in data:
                        _find(doc, buffer)
                elif isinstance(data, dict):
                    for key, value in data.items():
                        if not value: continue
                        buffer.update({item[0]: item for item in value})
                                    
                return buffer

            def _findItem(item, doc, buffer=None):
                if item not in doc: return buffer
                if item in buffer: return buffer
                name, typehint, (owner, _, info), show, ext = doc[item]
                
                if isinstance(info, list):
                    buffer.update({item: None, info[0]: None})
                else:
                    buffer.update({item: None})
                
                if ext:
                    for item in ext["map"]:
                        _findItem(item, varAll, buffer)
                        
                return buffer
            
            mapping, paramRule, strucRule = {}, [], []
            cls.parseDesc(docs, mapping, paramRule, strucRule)
            cls.cash(paramRule, mapping)
        
            valid = {} # 有效定义
            varAll = _find(paramRule)
            
            for name, typehint, (owner, _, info), show, ext in varAll.values():
                if isinstance(info, list):
                    valid.update({name: None, info[0]: None})
                else:
                    valid.update({name: None})
                    
                if not ext: continue
                for item in ext["map"]:
                    _findItem(item, varAll, valid)
            
            for item in [item for item in docs if item["name"] not in valid]:
                if item["name"] in limit: continue
                docs.remove(item)
                
        def __call(data):
            if isinstance(data, dict):
                for _, value in data.items():
                    return __call(value)
            elif isinstance(data, list):
                __invOpt(data)
                __refOpt(data)
            
        __call(describe.get("struct", {}))
    
    @classmethod
    def syntaxParse(cls, describe, head="data"):
        """语法解析"""
        def _split(syntax):
            sindex, eindex = [], []
            temp = {}
            
            for i, info in enumerate(syntax.split(".")):
                if info.startswith("f'"): sindex.append(i)
                if info.endswith("}'"): eindex.append(i+1)
                temp[i] = info
            
            if sindex:
                for si, ei in zip(sindex, eindex):
                    temp[si] = ".".join([temp[i] for i in range(si, ei)])
                    for i in range(si+1, ei):
                        del temp[i]
            
            return [v for _, v in sorted(temp.items(), key=lambda x: x[0])]
        
        syntax = [head]
        if not describe: return (SYMBOL.XPATH_SYMBOL, "", syntax)
        if describe == SYMBOL.XPATH_SYMBOL: return (SYMBOL.XPATH_SYMBOL, "", syntax + [None])
        if describe.startswith(SYMBOL.FUNC_SYMBOL): return (SYMBOL.FUNC_SYMBOL, "", describe[1:])
        
        if (not describe.startswith(SYMBOL.XPATH_SYMBOL)) and (not describe.startswith(SYMBOL.VAR_SYMBOL)):
            try:
                return (SYMBOL.XPATH_SYMBOL, "", ast.literal_eval(describe))
            except Exception as e:
                raise SyntaxError(repr(describe))
        
        if describe.startswith("$["):
            describe = f"{describe[0]}.{describe[1:]}"
        
        flag, *(describe) = _split(describe)
        
        for des in describe:
            if not des: continue
        
            if des.startswith("f'"):
                syntax.append(partial(cls.expParse, des))
                continue
        
            if "[" in des:
                index = des.find("[")
                val = des[:index]
                if val:                
                    syntax.append(cls.literalEval(val))
            else:
                index = -1
                syntax.append(cls.literalEval(des))
            
            while True:
                nextindex = des.find("]", index)
                if nextindex == -1: break
            
                val = des[index + 1:nextindex]
                if val.isdigit():
                    syntax.append(int(val))
                elif ":" in val:
                    xval = [cls.literalEval(x.strip()) for x in val.split(":")]
                    xval = [val if isinstance(val, int) else 0 for val in xval]
                    if len(xval) <= 2:
                        xval.append(0)
                    
                    if syntax[-1] == "*":
                        syntax = syntax[:-1]
                    
                    syntax.append(xval)
                else:
                    syntax.append(val)
                
                index = nextindex + 1
        
        # check
        for i, item in enumerate(syntax):
            if not isinstance(item, str): continue
            if not item.startswith("{") and not item.endswith("}"): continue
        
            if flag[0] != SYMBOL.VAR_SYMBOL:
                raise SyntaxError(f"Assignment expressions can only be in variables: '{'.'.join(describe)}'")
            if i <= 1:
                raise SyntaxError(f"The first cannot be an assignment expression: '{'.'.join(describe)}'")
            
            syntax[i] = [item[1:-1]]
            
        return (SYMBOL.XPATH_SYMBOL, flag[1:], syntax) if flag[0] == SYMBOL.XPATH_SYMBOL else (SYMBOL.VAR_SYMBOL, flag[1:], syntax[1:])
    
    @classmethod
    def ruleParse(cls, content):
        """规则解析"""
        value = content.get("value", "").strip()

        if SYMBOL.XPATH_SYMBOL == value:
            return cls.syntaxParse(value)
        elif "." in value:
            return cls.syntaxParse(value)
        else:
            value = cls.literalEval(value)

            if int(content.get("const", 1)) == 1:
                return (SYMBOL.XPATH_SYMBOL, "", value)
            if int(content.get("atom", 1)) == 1:
                return (SYMBOL.XPATH_SYMBOL, "", value)
            else:
                return (SYMBOL.XPATH_SYMBOL, "", "" if value else [])
            
    @classmethod
    def expParse(cls, content, mapping):
        """扩展表达式解析"""
        def replace(data, old, new):
            start = 0
            
            while True:
                idx = data.find(old, start)
                if idx == -1: break
                if (idx + len(new) - 2) >= len(data): break
            
                if ((data[idx-1] in symbols) or data[idx-1] in {"", " "}) and (data[idx + len(new) - 2]) in symbols:
                    start = idx + len(new)
                    data = data[:idx] + new + data[idx+len(old):]
                else:
                    start = idx + len(old)
            
            return data
        
        if not content: return {}
        
        special = []
        rcontent = content[:]
        symbols = ["(", ")", "[", "]", "{", "}", ":", ".", ",", "+", "-", "*", "/", ">", "<", "=", "|"]
        # check format
        compSet = "".join(mapping)
        # 分割信息
        temp = content[:]
        for symbol in symbols:
            if symbol == ".": continue
            temp = temp.replace(symbol, " ")
        # 信息匹配
        infos = temp.split(" ")
        for i, val in enumerate(infos):
            try:
                if (repr(val[:])[0] == '"') or (repr(val[:])[-1] == '"'): 
                    special.append(eval(val))
                    continue
            except Exception as e:
                pass
            
            ratio = difflib.SequenceMatcher(None, val, compSet).quick_ratio()
            if ratio and (ratio >= 0.2) and (val not in mapping):
                # print(f"Warning: Expression may have formatting problems, which may cause processing errors('{content}')")
                pass
            
            if val.startswith(f"{SYMBOL.XPATH_SYMBOL}.") or val.startswith(f"{SYMBOL.VAR_SYMBOL}."):
                if f"'{val}'" not in content:
                    content = replace(content, val, f"'{val}'")
                    
                cls.updateMap(mapping, [(val, cls.ruleParse({"value": val}))])
                
        # 匹配映射值
        symbols.append(" ")
        nmapping = {key: mapping[key] for key in mapping if (key in content) and (key not in special) and (not str(key).isdigit())}
        
        for item in list(nmapping):
            start = 0
            while True:
                sindex = rcontent.find(item, start)
                if sindex == 0 and (sindex + len(item)) < len(rcontent):
                    eindex = sindex + len(item)
                    if rcontent[eindex] in symbols:
                        sindex = sindex - 1
                else:
                    sindex = sindex - 1
                
                eindex = sindex + len(item) + 1
                
                if sindex <= -1: break
                if eindex >= len(rcontent): break
                if (rcontent[sindex] in symbols) and (rcontent[eindex] in symbols): break
                start = eindex
            
            if (sindex >= 0) and (rcontent[sindex] not in symbols):
                del nmapping[item]
            elif (len(rcontent) > eindex) and (rcontent[eindex] not in symbols):
                del nmapping[item]
            # elif sindex < -1:
                # del nmapping[item]
        
        # if not nmapping:
            # print(f"Warning: Expression invalid('{rcontent}')")
            
        return {"content": content, "map": nmapping}


class Format(Base, metaclass=Meta):
    """数据格式化"""
    REF = 0 # 全局引用计数
    KEYWORD = {"datas", "data", "mode", "struct"} # 内置关键字定义
    ENV = globals() # 全局环境
    
    @classmethod
    def help(cls):
        """帮助信息"""
        info = \
        f"""
        $       : XPATH 定义
        #       : VAR 定义
        KEYWORD : {cls.KEYWORD}
        注意: 所有的引用只能到 root, 所有动态赋值只能在变量中使用
              目前只支持两级数据结构
        """
        print(info)
        
    @classmethod
    def template(cls, hid, symbol, describe, idx_tag, filter_tag):
        """模板选择和生成"""
        mode = describe.get("mode", "index" if idx_tag else "default")
        mode = mode if mode else "default"
        
        if mode == "default":
            if describe.get("func") and filter_tag:
                mode = f"{mode}_func_filter"
            else:
                mode = f"{mode}_func" if describe.get("func") else f"{mode}_filter" if filter_tag else mode
        
        template = getattr(Template, mode, None)
        if not template: raise ERROR.TemplateNotExist(f"'{mode}' template Not Exist!")
        template = template().strip()
        
        cls.REF += 1
        
        template = template.replace("{$code}", hid)
        template = template.replace("{$name}", symbol if symbol else str(cls.REF))
        
        return template
        
    @classmethod
    def parseDesc(cls, describe, mapping, paramrule, strucrule, parent=None):
        """解析结构描述"""
        describe = describe.items() if isinstance(describe, dict) else [[None, describe]]
        
        for key, content in describe:
            if not parent:
                paramrule.append({"__parent__": key})
                strucrule.append({})
            
            if isinstance(content, dict):
                strucrule[-1][key] = "dict" if cls.isConts(content) else "slist"
                if key not in paramrule[-1]: paramrule[-1][key] = {}
                
                flag = {isinstance(x, str): None for x in content.values() if x != None}
                
                if False in flag:
                    for xkey, xval in content.items():
                        if isinstance(xval, (list, tuple)):
                            cls.parseDesc({xkey: xval}, mapping, [paramrule[-1][key]], strucrule, key)
                        elif isinstance(xval, dict):
                            xflag = {isinstance(x, str): None for x in xval.values() if x != None}
                            
                            if False in xflag:
                                strucrule[-1][xkey] = "dict"
                                paramrule[-1][key][xkey] = {}
                                cls.parseDesc(xval, mapping, [paramrule[-1][key][xkey]], strucrule, xkey)
                            else:
                                cls.parseDesc({xkey: xval}, mapping, [paramrule[-1][key]], strucrule, key)
                else:
                    paramrule[-1][key] = [content["name"], content.get("type", ""), cls.ruleParse(content), content.get("show", True), partial(cls.expParse, content.get("ext"))]
                    cls.updateMap(mapping, [(content["name"], paramrule[-1][key][2])])
            else:
                strucrule[-1][key] = "dlist"
                
                paramrule[-1][key] = [[doc["name"], doc.get("type", ""), cls.ruleParse(doc), doc.get("show", True), partial(cls.expParse, doc.get("ext"))] for doc in content]
                cls.updateMap(mapping, [(doc["name"], paramrule[-1][key][i][2]) for i, doc in enumerate(content)])

        return True
        
    @classmethod
    def parse(cls, symbol, describe):
        describe = deepcopy(describe)
        mapping, indexRule, paramRule, strucRule = {}, describe.get("index", []), [], []
        
        protocol = "list" if describe.get("protocol", True) else "dict"
        single = describe.get("single", False)
        
        STRUC.PROTOCOL = protocol
        STRUC.SINGLE = single
        
        if single and (cls.isVar(describe) or cls.isExt(describe)):
            # 不能出现变量间互相引用
            raise ERROR.SingleModeError("The Single mode does not support variable references and extended expressions.")
        
        if (not single) and MODE.OPT_MODE:
            tempMapping = {}
            cls.parseDesc(describe.get("struct", []), tempMapping, [], [])
            cls.updateMap(tempMapping, [(name, [cls.ruleParse({"value": x}) for x in value.split(",")] if "," in value else cls.ruleParse({"value": value})) for name, value in describe.get("_field", {}).items()])
            
            temp = cls.expParse(describe["filter"], tempMapping)
            cls.descOpt(describe, list(temp["map"].keys()) if temp else [])
        
        cls.parseDesc(describe.get("struct", []), mapping, paramRule, strucRule)
        cls.updateMap(mapping, [(name, [cls.ruleParse({"value": x}) for x in value.split(",")] if "," in value else cls.ruleParse({"value": value})) for name, value in describe.get("_field", {}).items()])
        
        datasRule = cls.syntaxParse(describe.get("datas"), "datas")
        filteRule = cls.expParse(describe.get("filter"), mapping)
        
        cls.cash(paramRule, mapping)

        for name in mapping:
            if name in cls.KEYWORD: raise ERROR.KeyWordError(name)
        
        HID = "".join([chr(random.randint(97, 122)) for _ in range(10)]) # 处理器ID
        
        param = {key: val for key, val in describe.items() if key not in cls.KEYWORD}
        param["pid"] = os.getpid()
        param["mode"] = "func" if describe.get("func") else "filter" if filteRule else ""
        
        template = cls.template(HID, symbol, describe, bool(indexRule), bool(filteRule))
        funcType, handler = Engine.make(cls.ENV, HID, template, datasRule, indexRule, paramRule, filteRule, strucRule)
        
        return funcType, partial(handler, **param)

    
# if __name__ == "__main__":
#     # key
#     # key and val
#     # 2 <= key <= 5 and (1 <= val <= 4)
#     # key in [1, 2, 3] or val in {2: 0, 3:0}
#     # key != 0 and val != 0
#     # 2 <= key <= 5 and val != 0
    
#     # x = ast.parse("x = [1,2,3]; y=x[0]")
#     # print(ast.dump(x))
#     # print(ast.unparse(x))
    
#     describe = {'datas': "$.index",
#                 'mode': '',
#                 "protocol": True,
#                 "single": False,
#                 'filter': "",
#                 "_field": {},
#                 'struct': {"doc": [{'name': 'hour', 'alias': '', 'type': 'list', 'value': '$.5.a'},
#                 {'name': 'hourA', 'alias': '', 'type': 'int', 'value': '$.5.a.1'},
#                 {'name': 'hourB', 'alias': '', 'type': 'int', 'value': '$.5.a.2'},
#                 {'name': 'hourC', 'alias': '', 'type': 'int', 'value': '$.2'},
#                 {'name': 'hourD', 'alias': '', 'type': 'str', 'value': '', 'ext': 'str($.5.a.1) + str(hourB)'},
#                 {'name': 'hourE', 'alias': '', 'type': 'int', 'value': '', 'ext': 'hourA + hourB'},
#                 {'name': 'hourF', 'alias': '', 'type': 'int', 'value': '', 'ext': '$.5.a.2 + $.5.a.1'},
#                 {'name': 'hourG', 'alias': '', 'type': 'int', 'value': '', 'ext': 'hour[2] + 123'},
#                 {'name': 'hourH', 'alias': '', 'type': 'int', 'value': '', 'ext': '#.hour.3.2 + #.hour.3.1'}]}}
    
#     datas = {'index': [('a0', 0, 0, 0, 0, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a1', 2, -1, 1, 1, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a2', 2, 0, 2, 2, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a3', 3, 3, 3, 3, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a4', 4, -4, 4, 4, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a5', 5, 5, 5, 5, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a6', 6, -6, 6, 6, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a7', 7, 7, 7, 7, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a8', 8, 8, 8, 8, {"a": [0, 1, 2, [1, 2, 3]]}),
#                       ('a9', 9, 9, 9, 9, {"a": [0, 1, 2, [1, 2, 3]]})]}
    
#     Format.help()
#     Format.SAFE_MODE = False
#     Format.DEBUG = True
#     Format.CYTHON_MODE = True
#     Format.TRYE_MODE = True
#     info, handler = Format.parse("DSS", describe)
#     import pprint
#     pprint.pprint(handler(datas))
    
#     ###########################################################################
#     describe = {'datas': "$.index",
#                 'mode': '',
#                 "protocol": True,
#                 "single": False,
#                 'filter': "",
#                 "_field": {"code": "$.1"},
#                 'struct': {
#                         'docs': [
#                                 {'name': 'keyx', 'alias': '', 'value': '100'}, 
#                                 {'name': 'keyy', 'alias': '', 'value': '[1,2,3]', 'ext': '1 if keyx else 0'},
#                                 {'name': 'key', 'alias': '', 'value': '$.1', 'ext': '-(key)'}, 
#                                 {'name': 'val', 'alias': '', 'value': '$.2'}, 
#                                 {'name': 'day', 'alias': '', 'value': '$.3'}, 
#                                 {'name': 'hour', 'alias': '', 'value': '$.4'},
#                                 {'name': 'hourX', 'alias': '', 'value': '', 'ext': '[1, 2, 3]'},
#                                 {'name': 'hourY', 'alias': '', 'value': '#.hourX.0'},
#                                 {'name': 'hourZ', 'alias': '', 'value': '#.day', 'ext': 'hourZ + 100'},
#                                 {'name': 'hourXX', 'alias': '', 'value': '', 'ext': 'hourY + hourZ'},
#                                 {'name': 'hourYY', 'alias': '', 'value': '#.key'},
#                                 {'name': 'hour', 'alias': '', 'value': '$.5.a'},
#                                 {'name': 'hourA', 'alias': '', 'value': '#.hour.0'},
#                                 {'name': 'hourB', 'alias': '', 'value': '#.hour.1'},
#                                 {'name': 'hourC', 'alias': '', 'value': '#.hour.2'}
#                                 ], 
#                         'docsx': {"docs": [{'name': 'keyx', 'alias': '', 'value': '100'}, 
#                                             {'name': 'keyx', 'alias': '', 'value': '[1,2,3]', 'ext': '1 if keyx else 0'},
#                                             {'name': 'key', 'alias': '', 'value': '$.1', 'ext': 'abs(key)'}, 
#                                             {'name': 'val', 'alias': '', 'value': '$.2'}, 
#                                             {'name': 'day', 'alias': '', 'value': '$.3'}, 
#                                             {'name': 'hour', 'alias': '', 'value': '$.4'},
#                                             {'name': 'hourX', 'alias': '', 'value': '#.key', 'ext': 'hourX + 100'}], 
#                                   'docids': {'name': 'cid', 'alias': '', 'value': '$.0', 'ext': '0 if code ==4 else cid'}, 
#                                     "A": {'name': 'keyx', 'alias': '', 'value': '100'}, 
#                                     "B": {"B1": {'name': 'keyx', 'alias': '', 'value': '100'},
#                                           "B2": [{'name': 'val', 'alias': '', 'value': '$.2'}, 
#                                                   {'name': 'day', 'alias': '', 'value': '$.3'}, 
#                                                   {'name': 'hour', 'alias': '', 'value': '$.4'}],
#                                           "B3": {"C": {"D": {"E": [{'name': 'keyx', 'alias': '', 'value': '100'}]}}}}},
#                         'docids': {'name': 'cid', 'alias': '', 'value': '$.0', 'ext': '0 if code == 2 else cid'}, 
#                         'xdocs': [{'name': 'cnt', 'alias': '', 'value': '1', 'const': '1', 'ext': '1 if cnt else 0'}],
#                         'xdocsx': {'name': 'xdocsx', 'alias': '', 'value': '1', 'const': '0', 'atom': '0'}, 
#                         "format": {'name': 'cid', 'alias': '', 'value': '[1,2,3]'},
#                         "lasttime": {'name': 'timestamp', 'alias': '', 'value': '0', "const":"0", "atom":"1"},
#                         "count": {'name': 'count', 'alias': '', 'value': '1', "const":"0"},
#                         "ext": {"index": {'name': 'cid', 'alias': '', 'value': '1'},
#                                 "list": {'name': 'cid', 'alias': '', 'value': '[3]'},
#                                 'docids': {'name': 'cid', 'alias': '', 'value': '$.0'},
#                                 'keys': [
#                                       {'name': 'cnt', 'alias': '', 'value': '$.0', 'ext': '0 if cnt[1] == "1" else cnt'},
#                                         ]
#                                 },
#                         "extx": {"name": "ext", "alias": "", "value": "[1]"},
#                         "ext1": [{"name": "ext", "alias": "", "value": "[1]"}],
#                         }}
    
#     datas = {'index': [('a0', 0, 0, 0, 0, {"a": [0, 1, 2]}),
#                       ('a1', 2, -1, 1, 1, {"a": [0, 1, 2]}),
#                       ('a2', 2, 0, 2, 2, {"a": [0, 1, 2]}),
#                       ('a3', 3, 3, 3, 3, {"a": [0, 1, 2]}),
#                       ('a4', 4, -4, 4, 4, {"a": [0, 1, 2]}),
#                       ('a5', 5, 5, 5, 5, {"a": [0, 1, 2]}),
#                       ('a6', 6, -6, 6, 6, {"a": [0, 1, 2]}),
#                       ('a7', 7, 7, 7, 7, {"a": [0, 1, 2]}),
#                       ('a8', 8, 8, 8, 8, {"a": [0, 1, 2]}),
#                       ('a9', 9, 9, 9, 9, {"a": [0, 1, 2]})]}
    
#     Format.help()
#     Format.DEBUG = True
#     Format.TRYE_MODE = True
#     info, handler = Format.parse("DSS", describe)
#     import pprint
#     pprint.pprint(handler(datas))
    
#     ###########################################################################
#     describe = {'datas': "$.index",
#                 'mode': '',
#                 "protocol": True,
#                 "single": False,
#                 'filter': "",
#                 "_field": {"_houseA": "$.1", "_hourB": "$.2", "_hourC": "$.3"},
#                 'struct': {
#                         'docs': [
#                                 {'name': 'hourA', 'alias': '', 'value': '', 'ext': '_houseA'},
#                                 {'name': 'hourB', 'alias': '', 'value': '', 'ext': '_hourB'},
#                                 {'name': 'hourC', 'alias': '', 'value': '', 'ext': '_hourC'}
#                                 ]}}
    
#     datas = {'index': [('a0', 0, 0, 0, 0, {"a": [0, 1, 2]}),
#                       ('a1', 2, -1, 1, 1, {"a": [0, 1, 2]}),
#                       ('a2', 2, 0, 2, 2, {"a": [0, 1, 2]}),
#                       ('a3', 3, 3, 3, 3, {"a": [0, 1, 2]}),
#                       ('a4', 4, -4, 4, 4, {"a": [0, 1, 2]}),
#                       ('a5', 5, 5, 5, 5, {"a": [0, 1, 2]}),
#                       ('a6', 6, -6, 6, 6, {"a": [0, 1, 2]}),
#                       ('a7', 7, 7, 7, 7, {"a": [0, 1, 2]}),
#                       ('a8', 8, 8, 8, 8, {"a": [0, 1, 2]}),
#                       ('a9', 9, 9, 9, 9, {"a": [0, 1, 2]})]}
    
#     Format.help()
#     Format.DEBUG = True
#     Format.FREE_MODE = True
#     Format.TRYE_MODE = True
#     info, handler = Format.parse("DSS", describe)
#     import pprint
#     pprint.pprint(handler(datas))
    
#     ###########################################################################
    
#     describe = {'datas': "$.index",
#                 'mode': '',
#                 "protocol": False,
#                 "single": False,
#                 'filter': "",
#                 "_field": {},
#                 'struct': {
#                         'docs': [
#                                 {'name': 'hour', 'alias': '', 'value': '$.5.a'},
#                                 {'name': 'hourA', 'alias': '', 'value': '$.0'},
#                                 {'name': 'hourB', 'alias': '', 'value': '$.1'},
#                                 {'name': 'hourC', 'alias': '', 'value': '$.2'}
#                                 ]}}
    
#     datas = {'index': [('a0', 0, 0, 0, 0, {"a": [0, 1, 2]}),
#                       ('a1', 2, -1, 1, 1, {"a": [0, 1, 2]}),
#                       ('a2', 2, 0, 2, 2, {"a": [0, 1, 2]}),
#                       ('a3', 3, 3, 3, 3, {"a": [0, 1, 2]}),
#                       ('a4', 4, -4, 4, 4, {"a": [0, 1, 2]}),
#                       ('a5', 5, 5, 5, 5, {"a": [0, 1, 2]}),
#                       ('a6', 6, -6, 6, 6, {"a": [0, 1, 2]}),
#                       ('a7', 7, 7, 7, 7, {"a": [0, 1, 2]}),
#                       ('a8', 8, 8, 8, 8, {"a": [0, 1, 2]}),
#                       ('a9', 9, 9, 9, 9, {"a": [0, 1, 2]})]}
    
#     Format.help()
#     Format.DEBUG = True
#     Format.TRYE_MODE = True
#     info, handler = Format.parse("DSS", describe)
#     import pprint
#     pprint.pprint(handler(datas))
    
#     ###########################################################################
#     describe = {'_field': {'IATA': '$.data.AIRPORTIATA',
#                 'IATAS': '$.data.AIRPORTIATA_1,$.data.AIRPORTIATA_2,$.data.AIRPORTIATA_3'},
#                 'datas': '',
#                 'filter': 'Flight_ID',
#                 'func': None,
#                 'mode': 'default',
#                 'protocol': True,
#                 'struct': [{'alias': '航班ID', 'name': 'Flight_ID', 'value': '$.data.ID'},
#                             {'alias': '任务性质',
#                             'name': 'Task_Type',
#                             'value': '$.data.TASK_TYPE[0]'},
#                             {'alias': '计划进港时间',
#                             'name': 'Planned_Arrival_Time',
#                             'value': "$.data.f'AIRPORTIATA_{len(IATA)}'"}]}
    
#     import datetime
#     datas = [{'schema': 'acdm', 'table': 'flight_dynamics', 'action': 'insert', 'data': {'SEQN': 1690519759394, 'SENDTIME': datetime.datetime(2023, 7, 28, 12, 49, 19), 'TYPE': 'DYNFLIGHT', 'SUBTYPE': 'MODIFY', 'FLIGHT_DATE': datetime.date(2023, 7, 28), 'REAL_DATE': datetime.datetime(2023, 7, 28, 12, 45), 'AIRLINE_NAME': 'HU', 'AIRLINE_ID': 69, 'BIZKEY': '20230728-HU7892-I-W/Z-202307281245', 'ID': 14528086, 'ARR_FLIGHT_ID': 14527776, 'DEP_FLIGHT_ID': None, 'IS_ARR_FLIGHT': '1', 'Flight_NO': 'HU7892', 'TASK_TYPE': 'W/Z', 'CRAFT_NO': 'B5905', 'CRAFTMODEL': 'A333', 'ENGINE_NO': '', 'ENGINE_TYPE': 'Trent 772B-60', 'AIRPORTIATA': 'URC', 'STAY_TYPE': '航后', 'ROUTEORDER_1': 1, 'ROUTEORDER_2': 2, 'ROUTEORDER_3': None, 'AIRPORTIATA_1': 'KHG', 'AIRPORTIATA_2': 'URC', 'AIRPORTIATA_3': None, 'REGION': 'REGI', 'REGION_1': 'DMST', 'REGION_2': None, 'REGION_3': None, 'PLAN_LANDING_1': None, 'ALT_LANDING_1': None, 'REAL_LANDING_1': None, 'PLAN_TAKEOFF_1': datetime.datetime(2023, 7, 28, 10, 55), 'ALT_TAKEOFF_1': None, 'REAL_TAKEOFF_1': datetime.datetime(2023, 7, 28, 11, 3), 'PLAN_LANDING_2': datetime.datetime(2023, 7, 28, 12, 45), 'ALT_LANDING_2': datetime.datetime(2023, 7, 28, 12, 40), 'REAL_LANDING_2': datetime.datetime(2023, 7, 28, 12, 39), 'PLAN_TAKEOFF_2': None, 'ALT_TAKEOFF_2': None, 'REAL_TAKEOFF_2': None, 'PLAN_LANDING_3': None, 'ALT_LANDING_3': None, 'REAL_LANDING_3': None, 'PLAN_TAKEOFF_3': None, 'ALT_TAKEOFF_3': None, 'REAL_TAKEOFF_3': None, 'ISALTERAIRPORT_1': 0, 'ISALTERAIRPORT_2': 0, 'ISALTERAIRPORT_3': None, 'ROUTE_ID1': 525935712, 'ROUTE_ID2': 525904274, 'ROUTE_ID3': 525904275, 'ROUTE_ID4': None, 'ROUTE_ID5': None, 'DMST_BUILDING': 'T2', 'INTER_BUILDING': None, 'GATE_NAME': None, 'SEATORDER_1': None, 'SEATORDER_2': None, 'SEATORDER_3': None, 'SEATORDER_4': None, 'SEATORDER_5': None, 'SEATNAME_1': None, 'SEATNAME_2': None, 'SEATNAME_3': None, 'SEATNAME_4': None, 'SEATNAME_5': None, 'OCCUPYTYPE_1': None, 'OCCUPYTYPE_2': None, 'OCCUPYTYPE_3': None, 'OCCUPYTYPE_4': None, 'OCCUPYTYPE_5': None, 'DRAGED_1': None, 'DRAGED_2': None, 'DRAGED_3': None, 'DRAGED_4': None, 'DRAGED_5': None, 'FLIGHT_STATE_CODE': 'PUBLISH', 'DTL_STATUS': 'ARR', 'ABN_STATUS': None, 'INNER_ABN_RSN': None, 'ABN_RSN': None, 'DELAY_TIME': 0, 'IMPACTED_AIRPORT': None, 'IMPACTED_ROUTEID': None, 'ARR_ROUTEID': 525935712, 'DEP_ROUTEID': 0, 'REAL_ARR_TIME_CUR': None, 'REAL_DEP_TIME_CUR': None, 'PLAN_DEP_TIME_PREV': datetime.datetime(2023, 7, 28, 10, 55), 'ALT_DEP_TIME_PREV': None, 'REAL_DEP_TIME_PREF': datetime.datetime(2023, 7, 28, 11, 3), 'STATUS_PREV_FLT': None, 'AIPORTIATA_PREV': 'KHG', 'ALT_ARR_TIME_CUR': None, 'AIRPORTIATA_FL': None, 'PLAN_ARR_TIME_FL': None, 'ALT_ARR_TIME_FL': None, 'REAL_ARR_TIME_FL': None, 'HASVIP': 0, 'VIP_AMT': None, 'HAS_DISBL': None, 'DISBL_AMT': None}}]

#     Format.help()
#     Format.DEBUG = True
#     Format.FREE_MODE = True
#     Format.TRYE_MODE = True
#     info, handler = Format.parse("DSS", describe)
#     import pprint
#     pprint.pprint(handler(datas))

#     ###########################################################################
#     describe = {'datas': '', 
#                 'protocol': True, 
#                 'index': {'key': "merge", "number": ""}, 
#                 "filter": None,
#                 'struct': [{'name': 'key', 'show': True, 'value': '', 'ext': '_key'}, 
#                             {'name': '_K_QV_1', 'show': False, 'value': '', 'ext': 'key'},
#                             {'name': 'number', 'show': True, 'value': '', 'ext': '_number'}, 
#                             {'name': 'start', 'show': True, 'value': '', 'ext': '_start'}, 
#                             {'name': 'end', 'show': True, 'value': '', 'ext': '_end'}, 
#                             {'name': 'cre_type', 'show': True, 'value': '', 'ext': '_cre_type'}, 
#                             {'name': 'cre_priority', 'show': True, 'value': '', 'ext': '_cre_priority'},
#                             {'name': 'cre_use', 'show': True, 'value': '', 'ext': '_cre_use'}, 
#                             {'name': 'user', 'show': True, 'value': '', 'ext': '_user'},
#                             {'name': 'license_type', 'show': True, 'value': '', 'ext': '_license_type'}, 
#                             {'name': 'limit', 'show': True, 'value': '', 'ext': '_limit'}, 
#                             {'name': 'practice', 'show': True, 'value': '', 'ext': '_practice'}], 
#                 '_field': {'_number': '$.0', '_start': '$.1', '_end': '$.2', '_cre_type': '$.3', '_cre_priority': '$.4', '_cre_use': '$.5', '_user': '$.6', '_license_type': '$.7', '_limit': '$.8', '_practice': '$.9', '_key': '$.10', '_xkey': '$.11'}}
    
#     Format.help()
#     Format.DEBUG = True
#     Format.SAFE_MODE = True
#     Format.TRYE_MODE = True
#     info, handler = Format.parse("DSS", describe)
