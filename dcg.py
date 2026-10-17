#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import ast
import sys
import types
import shutil
import tempfile
import importlib

try:
    import astunparse
except Exception:
    pass

from itertools import groupby
from collections import defaultdict


class ERROR:
    """错误定义"""
    class InputError(Exception): pass # 输入错误
    class VarRefError(Exception): pass # 变量引用错误
    class KeyWordError(Exception): pass # 关键词错误
    class ModeNotExist(Exception): pass # 模式不存在
    class VarRedefinition(Exception): pass # 变量重复定义
    class ImplementationNotSupport(Exception): pass # 实现不支持
    class SingleModeError(Exception): pass # single 模式错误
    class TemplateNotExist(Exception): pass # 模板定义不存在


class MODE:
    """运行模式定义"""
    DEBUG = False # 调试模式(输出全部编译执行的代码)
    OPT_MODE = True # 结构优化, 自动优化结构, 含 公共子表达式消除（CSE）、无效引用消除等
    FREE_MODE = False # 自由模式, 外部自行解析自定义结构的表达式, 然后传递给 Format, value 必须空, ext 都是引用, 所有字段都在 _field 中定义
    SAFE_MODE = False # 安全模式, 所有索引构建模式将转换安全模式(索引处理移交到外部处理, 外部需要定义 _safe 函数, 见示例)
    TRYE_MODE = False # 保护模式, 对与数据的处理将采用 try except 的方式进行保护, 结果除了输出正确结果外，还将提供错误数据信息
    CYTHON_MODE = True # Cython 模式, 根据情况优化执行效率


class SYMBOL:
    """符号定义"""
    XPATH_SYMBOL = "$" # 路径解析
    VAR_SYMBOL = "#" # 变量定义
    FUNC_SYMBOL = "@" # 函数调用


class STRUC:
    """结构定义"""
    PROTOCOL = True # 输出结果结构: list(True), dict(False) 
    SINGLE = False # 特殊输出结构, 展平单条(True), 原始结构(False)

    
class Template:
    """模板定义"""
    @staticmethod
    def default(*args, **kwargs):
        return """
def _inline_call_{$name}(datas, **kwargs):
    datas = None
    buffer_{$code} = None
    
    return buffer_{$code}
"""

    @staticmethod
    def default_func(*args, **kwargs):
        return """
def _inline_call_func_{$name}(datas, **kwargs):
    datas = None
    datas = tuple(kwargs["func"](datas)) 

    buffer_{$code} = None
    
    return buffer_{$code}
"""

    @staticmethod
    def default_filter(*args, **kwargs):
        return """
def _inline_call_filter_{$name}(datas, **kwargs):
    datas = None
    filter_{$code} = datas = [data for data in datas if data]
    
    buffer_{$code} = None
    
    return buffer_{$code}
"""

    @staticmethod
    def default_func_filter(*args, **kwargs):
        return """
def _inline_call_filter_{$name}(datas, **kwargs):
    datas = None
    datas = tuple(kwargs["func"](datas))
    filter_{$code} = datas = [data for data in datas if data]
    
    buffer_{$code} = None
    
    return buffer_{$code}
"""

    @staticmethod
    def debug(*args, **kwargs):
        return """
def _inline_call_debug_{$name}(datas, **kwargs):
    datas = {key: tuple(doc.items()) if isinstance(doc, dict) else tuple(doc) \
             for key, doc in datas.items()} if isinstance(datas, dict) else datas
    datas = None
    
    for data in datas:
        print("DATA:", data)
    
    mode = kwargs.get("mode")
    if mode == "func":
        datas = tuple(kwargs["func"]([datas]))
    elif mode == "filter":
        filter_{$code} = datas = [data for data in datas if data]
    
    buffer_{$code} = None
    
    print("RESULT:", buffer_{$code})
    
    return buffer_{$code}
""" 

    @staticmethod
    def index(*args, **kwargs):
        return """
def _inline_call_idx_{$name}(datas, **kwargs):
    datas = None
    datas = datas if datas else []
    index_{$code} = None
    buffer_{$code} = None
    
    return index_{$code}, [doc for doc in chain([i for d in index_{$code}.values() for i in d.values()])]
"""    


class BaseSyntaxOP():
    """基础语法树生成"""
    @classmethod
    def blurring(cls, obj):
        """屏蔽 python 版本间的差异"""
        if isinstance(obj, ast.AST): return obj
        
        if (sys.version_info.major == 3) and (sys.version_info.minor >= 8):
            return ast.Constant(obj, None)
        else:
            if isinstance(obj, str):
                return ast.Str(obj)
            elif isinstance(obj, int):
                return ast.Num(obj)
            elif obj == None:
                return ast.NameConstant(value=obj)
    
    @classmethod
    def getValue(cls, obj):
        if (sys.version_info.major == 3) and (sys.version_info.minor >= 8):
            return obj.value
        else:
            if isinstance(obj, ast.Str):
                return obj.s
            elif isinstance(obj, ast.Num):
                return obj.n
            elif isinstance(obj, ast.NameConstant):
                return obj.value
    @classmethod
    def createConst(cls, item):
        """创建常量
           item: 常量项, 如: 1 or [1, 2] or {"a": 1}
         """
        if isinstance(item, list):
            return ast.List([cls.createConst(x) for x in item], ast.Load())
        elif isinstance(item, tuple):
            return ast.Tuple([cls.createConst(x) for x in item], ast.Load())
        elif isinstance(item, dict):
            return ast.Dict([cls.createConst(key) for key in item.keys()], 
                            [cls.createConst(value) for value in item.values()])
        else:
            return cls.blurring(item)
        
    @classmethod
    def createVar(cls, item, flag=True):
        """创建变量
           item: 变量名
           flag: 当作为独立变量时应为 True, 引用变量应为 False， 如 ： x(True) = 0 or for doc(True) in data or data[1](False)
        """
        return ast.Name(item, ast.Store() if flag else ast.Load())
        
    @classmethod
    def createJSON(cls, idxpaths, sliceinfo=None, *args, **kwargs):
        """创建 JSON 调用"""
        flag = True if isinstance(idxpaths[0], (str, int)) else False
        path = ".".join([str(x) for x in idxpaths[1:] + [sliceinfo]])
        node = ast.Call(ast.Attribute(cls.createVar(idxpaths[0], False) if flag else idxpaths[0], "get", ast.Load()), [ast.Constant(value=path, kind=None)], [])

        return node
        
    @classmethod
    def createIdx(cls, owner, dtype, idxpaths, sliceinfo=None, *args, **kwargs):
        """创建索引项
            idxpath: 索引路径 [(item1, itype), (item2, itype))], 注意: itype 为 0(常量) or 1(变量引用) 
                     如: [("data", 0), ("datax", 0)] == {"data": {"datax": [1, 2, 3]}}
                         [("data", 0), ("datax", 0), (0, 0)] == {"data": {"datax": [[1, 2, 3], [4, 5, 6]]}}
            sliceinfo: 切片信息, 如: [1, 2, 0] == x["data"]["datax"][1:2:0]
        """
        if dtype == "json": return cls.createJSON(idxpaths, sliceinfo)
        if not isinstance(idxpaths, (list, tuple)): return cls.createConst(idxpaths)

        idxpaths = [item if isinstance(item, (list, tuple)) else (item, 0) for item in idxpaths]
        idxpaths = [(item, 1) if len(item) == 1 else item for item in idxpaths]
        
        if not idxpaths and sliceinfo: return cls.createVar(sliceinfo, False)
        if not idxpaths: return cls.createConst([])
        
        if kwargs.get("safe", True) and MODE.SAFE_MODE: # callback 移交至外部处理
            args = []
            for i, (item, itype) in enumerate(idxpaths):
                if i == 0:
                    args.append(cls.createVar(item, False))
                elif isinstance(item, list):
                    args.append(cls.createVar(item[0], False))
                else:
                    args.append(cls.createVar(item, False) if itype else cls.createConst(item))
            
            if sliceinfo != None:
                if isinstance(sliceinfo, list):
                    if len(sliceinfo) == 3:
                        if sliceinfo[2] == 0: sliceinfo[2] = 1
                        args.append(ast.List([cls.blurring(x) for x in sliceinfo], ast.Load()))
                    else:
                        args.append(cls.createVar(sliceinfo[0], False))
                else:
                    args.append(cls.blurring(sliceinfo))
            
            return ast.Call(func=ast.Name(id="_safe", ctx=ast.Load()), args=args, keywords=[])
        
        for i, (item, itype) in enumerate(idxpaths):
            if i == 0:
                node = cls.createVar(item, False)
            elif isinstance(item, list):
                node = ast.Subscript(node, ast.Index(cls.createVar(item[0], False)), ast.Load())
            elif item == "$json":
                return cls.createIdx(owner, "json", [node] + [item for item, _ in idxpaths[i+1:]], sliceinfo)
            else:
                node = ast.Subscript(node, ast.Index(cls.createVar(item, False) if itype else cls.createConst(item)), ast.Load())
        
        if sliceinfo != None:
            if isinstance(sliceinfo, list):
                if len(sliceinfo) == 3:
                    lower = None if sliceinfo[0] == 0 else cls.blurring(sliceinfo[0])
                    upper = None if sliceinfo[1] == 0 else cls.blurring(sliceinfo[1])
                    step = None if sliceinfo[2] == 0 else cls.blurring(sliceinfo[2])
                    return ast.Subscript(node, ast.Slice(lower, upper, step), ast.Load())
                else:
                    return ast.Subscript(node, ast.Index(cls.createVar(sliceinfo[0], False)), ast.Store())
            else:
                return ast.Subscript(node, ast.Index(cls.blurring(sliceinfo)), ast.Load())
        
        return node
    
    @classmethod
    def createGenerate(cls, vars, iterinfo, varname, retinfo, gtype=None):
        """创建生成式
            vars :变量信息
            iterinfo: 索引项描述, 见 createIdx
            varname: 中间值变量名, 如: "data"
            retinfo: 结果值描述 (retype, infos), infos 见 createIdx
            gtype: 生成式类型(列表或元组), 默认 列表生成式
        """
        def __tranf(lvars, infos, flag):
            temp = []
            
            for tag, name, typehint, (owner, dtype, info), show in infos:
                if flag and (not show): continue
                
                if lvars and name in lvars:
                    temp.append(cls.createVar(name, False))
                elif tag == 0:
                    temp.append(cls.createConst(info))
                elif tag == 1:
                    temp.append(cls.createIdx(owner, dtype, *info))
                else:
                    temp.append(info)
            
            return temp
        
        retype, retinfos = retinfo
        
        if retype == "atom":
            _, _, _, (owner, dtype, infos), _ = retinfos[0]
            elt = cls.createIdx(owner, dtype, *infos) if isinstance(infos, (list, tuple)) else infos
        elif retype == "const":
            _, _, _, (owner, dtype, infos), _ = retinfos[0]
            elt = cls.createConst(*infos)
        elif (retype == "list") or (retype == "tuple"):
            if isinstance(retinfos, (list, tuple)):
                if retype == "list":
                    elt = ast.List(__tranf(vars, retinfos, True), ast.Load())
                elif retype == "tuple":
                    elt = ast.Tuple(__tranf(vars, retinfos, True), ast.Load())
            else:
                elt = retinfos
        elif retype == "dict":
            if isinstance(retinfos, (list, tuple)):
                elt = ast.Dict([cls.createConst(name) for _, name, _, _, show in retinfos if show], __tranf(vars, retinfos, True))
            else:
                elt = retinfos
                
        if not iterinfo: return elt
        
        if ((not vars) or (retype in ["atom", "const"])) and (not MODE.TRYE_MODE):
            if isinstance(retinfos, (list, tuple)):
                infosX = sorted(filter(lambda x: (x[1] not in vars) and (not x[-1]), retinfos))
                varInfoX = [val.test for (_, name, *_), val in zip(infosX, __tranf(None, infosX, False)) if isinstance(val, ast.If)]
            else:
                varInfoX = []
                
            generators = [ast.comprehension(cls.createVar(varname), cls.createIdx("", "", *iterinfo), varInfoX, 0)] # 最后两个参数: ifs, is_async
            return ast.GeneratorExp(elt, generators) if gtype else ast.ListComp(elt, generators)
        else:
            if isinstance(retinfos, (list, tuple)):
                infos = sorted(filter(lambda x: x[1] in vars, retinfos), key=lambda x: vars.index(x[1]))
                infosX = sorted(filter(lambda x: (x[1] not in vars) and (not x[-1]), retinfos))
                varInfo = [val if isinstance(val, ast.If) else ast.Assign([cls.createVar(name)], val, lineno=0) for (_, name, *_), val in zip(infos, __tranf(None, infos, False))]
                varInfoX = [val if isinstance(val, ast.If) else ast.Assign([cls.createVar(name)], val, lineno=0) for (_, name, *_), val in zip(infosX, __tranf(None, infosX, False))]
                # exprInfo = ast.Expr(ast.Call(ast.Attribute(ast.Name("undefined", ast.Load()), "append", ast.Load()), [elt], []))
            else:
                varInfo = varInfoX = []
            
            exprInfo = ast.Expr(ast.Call(ast.Name("undefined", ast.Load()), [elt], []))
            
            return ast.For(cls.createVar(varname), cls.createIdx("", "", *iterinfo), varInfo + varInfoX + [exprInfo], [], lineno=0)

    @classmethod
    def tranfIFS(cls, nodes, infos, attrname="value"):
        """对 IF 简写表达式进行转换
        temp = ast.parse("key and val")
        BaseSyntaxOP.tranfIFS(temp.body, {"key": ["data", 1], "val": ["data", 2]})
        node.value.generators[0].ifs = [temp.body[0].value]
        """
        for node in nodes:
            if isinstance(node, ast.Expr):
                node.value.__parent = node
                cls.tranfIFS([node.value], infos, "value")
            elif isinstance(node, ast.IfExp):
                if node.test:
                    node.test.__parent = node
                    cls.tranfIFS([node.test], infos, "test")
                if node.body:
                    node.body.__parent = node
                    cls.tranfIFS([node.body], infos, "body")
                if node.orelse:
                    node.orelse.__parent = node
                    cls.tranfIFS([node.orelse], infos, "orelse")
            elif isinstance(node, ast.If):
                if node.test:
                    node.test.__parent = node
                    cls.tranfIFS([node.test], infos, "test")
                if node.body:
                    for i, xnode in enumerate(node.body):
                        xnode.__parent = (node.body, i)
                        
                    cls.tranfIFS(node.body, infos, "body")
                if node.orelse:
                    for i, xnode in enumerate(node.orelse):
                        xnode.__parent = (node.orelse, i)
                        
                    cls.tranfIFS(node.orelse, infos, "orelse")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    node.func.__parent = node
                    cls.tranfIFS([node.func], infos, "func")
                if node.args:
                    for i, xnode in enumerate(node.args):
                        xnode.__parent = (node.args, i)
                    
                    cls.tranfIFS(node.args, infos, None)
            elif isinstance(node, ast.Compare):
                if isinstance(node.left, ast.Name):
                    owner, dtype, info = infos[node.left.id]
                    node.left = cls.createIdx(owner, dtype, info[:-1], info[-1])
                elif isinstance(node.left, ast.Subscript):
                    node.left.value.__parent = node.left
                    cls.tranfIFS([node.left.value], infos, "value")
                elif isinstance(node.left, ast.Call):
                    for i, xnode in enumerate(node.left.args):
                        xnode.__parent = (node.left.args, i)
                    
                    cls.tranfIFS(node.left.args, infos, None)
                elif isinstance(node.left, ast.Constant):
                    if node.left.value in infos:
                        owner, dtype, info = infos[node.left.value]
                        node.left = cls.createIdx(owner, dtype, info[:-1], info[-1])
                elif isinstance(node.left, ast.Str):
                    owner, dtype, info = infos[node.left.s]
                    node.left = cls.createIdx(owner, dtype, info[:-1], info[-1])
                
                if node.comparators:
                    for i, xnode in enumerate(node.comparators):
                        xnode.__parent = (node.comparators, i)
                    
                    cls.tranfIFS(node.comparators, infos, None)
            elif isinstance(node, ast.comprehension):
                node.iter.__parent = node
                cls.tranfIFS([node.iter], infos, "iter")
                
                for i, xnode in enumerate(node.ifs):
                    xnode.__parent = (node.ifs, i)
                
                cls.tranfIFS(node.ifs, infos, None)
            elif isinstance(node, (ast.List, ast.Tuple)):
                for i, xnode in enumerate(node.elts):
                    xnode.__parent = (node.elts, i)
                
                cls.tranfIFS(node.elts, infos, None)
            elif isinstance(node, ast.ListComp):
                node.elt.__parent = node
                cls.tranfIFS([node.elt], infos, "elt")
                
                for i, xnode in enumerate(node.generators):
                    xnode.__parent = (node.generators, i)
                
                cls.tranfIFS(node.generators, infos, None)
            elif isinstance(node, ast.Dict):
                for i, xnode in enumerate(node.keys):
                    xnode.__parent = (node.keys, i)
                cls.tranfIFS(node.keys, infos, "keys")
                
                for i, xnode in enumerate(node.values):
                    xnode.__parent = (node.values, i)
                cls.tranfIFS(node.values, infos, "values")
            elif isinstance(node, ast.Index):
                node.value.__parent = node
                cls.tranfIFS([node.value], infos, "value")
            elif isinstance(node, ast.Slice):
                if node.lower:
                    node.lower.__parent = node
                    cls.tranfIFS([node.lower], infos, "lower")
                if node.upper:
                    node.upper.__parent = node
                    cls.tranfIFS([node.upper], infos, "upper")
                if node.step:
                    node.step.__parent = node
                    cls.tranfIFS([node.step], infos, "step")
            elif isinstance(node, ast.Starred):
                node.value.__parent = node
                cls.tranfIFS([node.value], infos, "value")
            elif isinstance(node, ast.Name):
                if node.id not in infos: continue
            
                if isinstance(infos[node.id], list):
                    temp = ast.Tuple([cls.createIdx(owner, dtype, info) for owner, dtype, info in infos[node.id]], ast.Load())
                else:
                    owner, dtype, info = infos[node.id]
                    
                    if isinstance(info, (list, tuple)):
                        if owner == SYMBOL.XPATH_SYMBOL:
                            if info[0] == "data":
                                temp = cls.createIdx(owner, dtype, info[:-1], info[-1])
                            else:
                                temp = cls.createConst(info)
                        else:
                            temp = cls.createIdx(owner, dtype, info[:-1], info[-1])
                    else:
                        temp = cls.createIdx(owner, dtype, info)
                
                if isinstance(node.__parent, tuple):
                    node.__parent[0][node.__parent[1]] = temp
                else:
                    setattr(node.__parent, attrname, temp)
            elif isinstance(node, ast.JoinedStr):
                for i, xnode in enumerate(node.values):
                    xnode.__parent = (node.values, i)
                
                cls.tranfIFS(node.values, infos, "None")
            elif isinstance(node, ast.FormattedValue):
                node.value.__parent = node
                cls.tranfIFS([node.value], infos, "value")
            elif isinstance(node, ast.Attribute):
                node.value.__parent = node
                cls.tranfIFS([node.value], infos, "value")
            elif isinstance(node, ast.Subscript):
                if node.value:
                    node.value.__parent = node
                    cls.tranfIFS([node.value], infos, "value")
                if node.slice:
                    node.slice.__parent = node
                    cls.tranfIFS([node.slice], infos, "slice")
            elif isinstance(node, ast.BoolOp):
                for i, xnode in enumerate(node.values):
                    xnode.__parent = (node.values, i)
                
                cls.tranfIFS(node.values, infos, None)
            elif isinstance(node, ast.UnaryOp):
                node.operand.__parent = node
                cls.tranfIFS([node.operand], infos, "operand")
            elif isinstance(node, ast.BinOp):
                node.left.__parent = node
                cls.tranfIFS([node.left], infos, "left")
                node.right.__parent = node
                cls.tranfIFS([node.right], infos, "right")
            elif isinstance(node, ast.Constant):
                if node.value in infos:
                    owner, dtype, info = infos[node.value]
                    temp = cls.createIdx(owner, dtype, info[:-1], info[-1])
                    
                    if isinstance(node.__parent, tuple):
                        node.__parent[0][node.__parent[1]] = temp
                    else:
                        setattr(node.__parent, attrname, temp)
            elif isinstance(node, ast.Str):
                if node.s in infos:
                    owner, dtype, info = infos[node.s]
                    temp = cls.createIdx(owner, dtype, info[:-1], info[-1])
                    
                    if isinstance(node.__parent, tuple):
                        node.__parent[0][node.__parent[1]] = temp
                    else:
                        setattr(node.__parent, attrname, temp)
                
    
class TypeVisitor(ast.NodeVisitor):
    """类型推断"""
    def __init__(self):
        self.varTypes = {} # 变量
        self.loopVars = set() # 循环变量
        self.dictLiterals = {} # 字段字面量
        self.seenTypes = {} # 类型冲突
        self.subscript_int_count = defaultdict(int)
    
    def visit_Assign(self, node):
        inferred = self.infer_type(node.value)
        
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.update_vartype(target.id, inferred)

                if isinstance(node.value, ast.Dict):
                    key_types = {}
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            key_types[k.value] = self.infer_type(v)
                    if key_types:
                        self.dictLiterals[target.id] = key_types
                        
        self.generic_visit(node)
    
    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name) and node.annotation:
            name = node.target.id
            if isinstance(node.annotation, ast.Name):
                ty = node.annotation.id.lower()
                if ty in ("list", "dict", "int", "float", "str", "bint", "object"):
                    self.update_vartype(name, ty)
                    return
                
        self.generic_visit(node)

    def visit_Subscript(self, node):
        is_int_index = False
        if isinstance(node.slice, ast.Index):  # Python < 3.9
            index_val = node.slice.value
        elif isinstance(node.slice, ast.Constant):  # Python 3.9+
            index_val = node.slice
        else:
            index_val = node.slice  # 其他情况忽略
    
        if isinstance(index_val, ast.Constant) and isinstance(index_val.value, int):
            is_int_index = True
    
        if is_int_index and isinstance(node.value, ast.Name):
            self.subscript_int_count[node.value.id] += 1
    
        self.generic_visit(node)
        
    def visit_For(self, node):
        if isinstance(node.target, ast.Name):
            self.loopVars.add(node.target.id)
            
        self.generic_visit(node)
    
    def update_vartype(self, name, ty):
        if ty is None: return
        
        self.seenTypes.setdefault(name, set()).add(ty)
        current = self.varTypes.get(name)
        
        if current is None:
            self.varTypes[name] = ty
        elif current == ty:
            pass
        else:
            if current == "object" and ty != "object":
                self.varTypes[name] = ty
            elif ty == "object" and current != "object":
                pass
            else:
                self.varTypes[name] = "object"
    
    def infer_type(self, expr):
        if isinstance(expr, ast.Constant):
            val = expr.value
            if isinstance(val, bool): return "int"
            if isinstance(val, int): return "int"
            if isinstance(val, float): return "float"
            if isinstance(val, str): return "str"
            return "object"
        elif isinstance(expr, ast.List):
            return "list"
        elif isinstance(expr, ast.Tuple):
            return "list"
        elif isinstance(expr, ast.Set):
            return "list"
        elif isinstance(expr, ast.Dict):
            return "dict"
        elif isinstance(expr, ast.Name):
            known = self.varTypes.get(expr.id)
            if known: return known
            if expr.id in ("datas",): return "list"
            return "object"
        elif isinstance(expr, ast.Subscript):
            # 如果下标是常量且左侧是已知字典且有字典字面量信息
            if isinstance(expr.slice, ast.Index):  # Python<3.9
                sub = expr.slice.value
            elif isinstance(expr.slice, ast.Constant):  # Python 3.9+
                sub = expr.slice
            else:
                sub = expr.slice  # 处理 Slice 等特殊情况，回退
            if isinstance(expr.value, ast.Name) and isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                var_name = expr.value.id
                if var_name in self.dictLiterals:
                    key_type = self.dictLiterals[var_name].get(sub.value)
                    if key_type: return key_type
            container_type = self.infer_type(expr.value)
            if container_type in ("list", "dict", "str"):
                return "object"
            return "object"
        elif isinstance(expr, ast.Attribute):
            obj_type = self.infer_type(expr.value)
            attr = expr.attr
            if obj_type == "dict":
                if attr in ("keys", "values", "items"): return "list"
            if obj_type == "str":
                if attr in ("upper", "lower", "strip", "replace", "capitalize", "title", "swapcase"): return "str"
                if attr == "split": return "list"
            if obj_type == "list":
                if attr == "pop": return "object"
                if attr == "copy": return "list"
                if attr == "append": return "object"  # 方法对象
            return "object"
        elif isinstance(expr, ast.Call):
            fn = expr.func
            if isinstance(fn, ast.Name):
                name = fn.id
                if name in ("int", "float", "str", "bool", "bytes", "complex"): return name
                if name in ("list", "dict", "tuple", "set"): return name
                if name in ("len", "abs", "round", "min", "max", "sum", "any", "all"): return "int"
                if name == "isinstance": return "int"
                if name == "type": return "object"
                if name == "print": return "object"
            if isinstance(fn, ast.Attribute):
                obj_type = self.infer_type(fn.value)
                if obj_type == "dict" and fn.attr == "get": return "object"
                if obj_type == "str" and fn.attr in ("join", "replace", "strip"): return "str"
            return "object"
        elif isinstance(expr, ast.BinOp):
            left_t = self.infer_type(expr.left)
            right_t = self.infer_type(expr.right)
            op = expr.op.__class__.__name__
            if op in ("Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow"):
                if left_t == right_t == "int": return "int"
                if "float" in (left_t, right_t): return "float"
                if left_t == "str" and right_t == "str": return "str"
                if left_t == "list" and right_t == "list": return "list"
                return "object"
            if op in ("Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"): return "int"
            if op in ("And", "Or"): return "object"
            return "object"
        elif isinstance(expr, ast.UnaryOp):
            op = expr.op.__class__.__name__
            t = self.infer_type(expr.operand)
            if op in ("UAdd",): return t
            if op in ("USub",): return t if t in ("int", "float") else "object"
            if op in ("Not", "Invert"): return "int" if t == "int" else "object"
            return "object"
        elif isinstance(expr, ast.IfExp):
            body_t = self.infer_type(expr.body)
            orelse_t = self.infer_type(expr.orelse)
            if body_t == orelse_t: return body_t
            return "object"
        
        elif isinstance(expr, ast.ListComp): return "list"
        elif isinstance(expr, ast.DictComp): return "dict"
        elif isinstance(expr, ast.SetComp): return "list"
        elif isinstance(expr, ast.GeneratorExp): return "object"
        elif isinstance(expr, ast.JoinedStr): return "str"
        
        return "object"
    

class PY2Cython():
    """转 Cython, 以提升执行效率 20 ~ 30 %"""
    @classmethod
    def __py_to_cython(cls, env, source, funcname, typehints=None):
        """AST分析 + 注入 cdef 声明，生成高优化 Cython 源码"""
        funcNode = None
        
        try:
            exprAst = ast.parse(source)
            
            for node in exprAst.body:
                if isinstance(node, ast.FunctionDef) and node.name == funcname:
                    funcNode = node
                    break
        except SyntaxError:
            return cls.__py_to_cython_simple(source, funcname)
        
        if not funcNode:
            return cls.__py_to_cython_simple(source, funcname)

        # 获取函数参数
        params = set()
        for arg in funcNode.args.args:
            params.add(arg.arg)
        if funcNode.args.vararg:
            params.add(funcNode.args.vararg.arg)
        if funcNode.args.kwarg:
            params.add(funcNode.args.kwarg.arg)

        # 变量类型解析
        vartype = TypeVisitor()
        vartype.visit(funcNode)
        
        varTypes = vartype.varTypes
        for var_name, count in vartype.subscript_int_count.items():
            if count >= 2 and var_name not in vartype.loopVars:
                current = varTypes.get(var_name)
                if current is None or current == "object":
                    varTypes[var_name] = "list"
        # 循环变量, 强制设为 object, 不应该受 varTypes 影响
        for lv in vartype.loopVars:
            vartype.varTypes[lv] = "object"

        # 生成声明
        declare = []
        for name in sorted(varTypes):
            if name in params: continue
            if name in vartype.loopVars: continue
            cdeftype = typehints[name] if typehints and (name in typehints) else varTypes[name]
            declare.append(f"    cdef {cdeftype} {name}")
            
        # 插入声明
        pattern = re.compile(rf"^def\s+{re.escape(funcname)}\s*\(", re.MULTILINE)
        match = pattern.search(source)
        if not match:
            return PY2Cython.__py_to_cython_simple(source, funcname)
    
        colonPos = source.find("):", match.start())
        if colonPos == -1:
            colonPos = source.find(":", match.start())
        if colonPos == -1:
            return PY2Cython.__py_to_cython_simple(source, funcname)

        start = source.index("\n", colonPos) + 1
        indent_match = re.match(r"(\s+)", source[start:])
        indent = indent_match.group(1) if indent_match else "    "

        if declare:
            insertion = ""
            for line in declare:
                insertion += indent + line.strip() + "\n"
            msource = source[:start] + insertion + source[start:]
        else:
            msource = source
            
        # 消除 list.append 的临时绑定
        append_map = {}
        for m in re.finditer(r"\b(\w+_append)\s*=\s*(\w+)\s*\.append\b", msource):
            append_var = m.group(1)
            list_var = m.group(2)
            append_map[append_var] = list_var
        
        for app_var, lst_var in append_map.items():
            msource = re.sub(r"\b" + re.escape(app_var) + r"\(", lst_var + ".append(", msource)
        
        msource = re.sub(r"^\s*\w+_append\s*=\s*\w+\s*\.append\s*\n", "", msource, flags=re.MULTILINE)
        for app_var in append_map.keys():
            msource = re.sub(r"^\s*cdef\s+object\s+" + re.escape(app_var) + r"\s*\n", "", msource, flags=re.MULTILINE)

        # 添加头文件和装饰器
        preamble = (
            "# cython: infer_types=True\n"
            "# cython: nonecheck=False\n"
            "import cython\n"
            "from cpython cimport list, dict\n\n")
        decorators = (
            "@cython.boundscheck(False)\n"
            "@cython.wraparound(False)\n"
            "@cython.cdivision(True)\n")
        
        match = re.search(rf"^def\s+{re.escape(funcname)}\s*\(", msource, re.MULTILINE)
        if match:
            start = match.start()
            msource = msource[:start] + decorators + msource[start:]

        return preamble + msource

    @classmethod
    def __py_to_cython_simple(cls, source, funcname):
        """简单降级方案"""
        preamble = "import cython\nfrom cpython cimport list\n\n"
        decorators = "@cython.boundscheck(False)\n@cython.wraparound(False)\n"
        msource = re.sub(rf"(def {re.escape(funcname)}\(.*?\):)", decorators + r"\1", source, count=1)
      
        return preamble + msource

    @classmethod
    def __compile_cython(cls, source, modulename, funcname):
        """编译 Cython 源码并返回函数对象"""
        tmpdir = tempfile.mkdtemp()
        pyx_path = os.path.join(tmpdir, f"{modulename}.pyx")
        
        try:
            with open(pyx_path, "w") as f:
                f.write(source)
                
            import pyximport
            
            pyximport.install(language_level=3)
            sys.path.insert(0, tmpdir)
            mod = importlib.import_module(modulename)
            func = getattr(mod, funcname)
        finally:
            sys.path.pop(0)
            shutil.rmtree(tmpdir, ignore_errors=True)
            
        return func

    @classmethod
    def make(cls, env, hid, source, funcname, typehints=None):
        csource = cls.__py_to_cython(env, source, funcname, typehints)
        handler = cls.__compile_cython(csource, f"_cython_{hid}", funcname)
        
        lines = csource.splitlines()
        for i, line in enumerate(lines, 1):
            print(f"{i:<4d} {line}")
        print("\n")
        
        return handler


class Engine(ast.NodeTransformer):
    ID = None
    FUNC_TYPE = None
    FUNC_NODE = None
    DATAS_RULE = None
    INDEX_RULE = None
    PARAM_RULE = None
    FILTER_RULE = None
    STRUC_RULE = None
    TYPE_HINTS = {}
    
    def find_Var(self, rawinfos):
        """依赖分析-权重计算-分组排序, 查找局部变量, 返回有依赖关系序的局部变量"""
        def __ref(name, depend, ref=None):
            if not depend.get(name): return ref
            for next in depend[name]:
                return __ref(next, depend, ref + 1 if ref else 1)
        
        lvars, depend = set(), {}
        
        for name, typehint, (owner, _, info), _, ext in rawinfos:
            if ext:
                for item, infox in ext["map"].items():
                    if name == item: continue
                    if item.startswith(f"{SYMBOL.VAR_SYMBOL}."): continue
                
                    for ownerx, _, val in infox if isinstance(infox, list) else [infox]:
                        if MODE.FREE_MODE and (ownerx != SYMBOL.VAR_SYMBOL and val != ""): continue
                        lvars.add(item)
                        depend.setdefault(name, {}).setdefault(item, {})
            
            if owner != SYMBOL.VAR_SYMBOL: continue
        
            for i, item in enumerate(info):
                if i == 0 or isinstance(item, list):
                    item = item[0] if isinstance(item, list) else item
                    if item == SYMBOL.VAR_SYMBOL: continue
                    if isinstance(item, (int, float)): continue
                    lvars.add(item)
                    depend.setdefault(name, {}).setdefault(item, {})
        
        if lvars:
            ref = {name: __ref(name, depend) for name in depend}
            ref = {name: sum([ref[name], sum([ref.get(xname, 0) for xname in doc])]) for name, doc in depend.items()}
            
            lvars = sorted([[item, ref.get(item, 0)] for item in lvars], key=lambda x: x[1])
            lvars = [var for _, group in groupby(lvars, key=lambda x: x[1]) for var, _ in sorted(group, key=lambda x: x[0])]
            
            for var, _ in sorted(ref.items(), key=lambda x: x[1]):
                if var in lvars: continue
                # if len(depend[var]) == 1:
                #     refVar = list(depend[var].keys())[0]
                #     if refVar not in lvars: continue
                #     lvars.insert(lvars.index(refVar) + 1, var)
                if var.startswith("_"):
                    index = max([lvars.index(name) for name in depend[var]])
                    lvars.insert(index + 1, var)
            
            olvars = lvars[:]
            
            # 优化依赖顺序
            for var in lvars[:]:
                if var not in depend: continue
                if not all([name in lvars for name in depend[var]]): continue
                lvars.remove(var)
                index = max([lvars.index(name) for name in depend[var]])
                lvars.insert(index + 1, var)

            oscore = sum([i for i, v in enumerate(olvars) if v.startswith("_")])
            score = sum([i for i, v in enumerate(lvars) if v.startswith("_")])
            
            return olvars if oscore < score else lvars
        else:
            return []
    
    def struct_Tranf(self, vid, pkeys, xkey, xval, idx):
        """结构转换"""
        nxkey, nxval, temp = [], [], []
        
        for pkey, key, node in zip(pkeys, xkey, xval):
            if isinstance(node, ast.For):
                temp.append([pkey, key, node])
                node = ast.List([], ast.Load())
            nxkey.append(key)
            nxval.append(node)
            
        for pkey, key, node in temp:
            parent = [p.value for p in pkey] if pkey[-1] != key else []
            var = f"{'_'.join(parent + [BaseSyntaxOP.getValue(key)])}_{self.ID}"
            var2 = f"{var}_append"
            
            node.body[-1].value.func = BaseSyntaxOP.createVar(var2, False)
            self.FUNC_NODE.body.insert(idx, ast.Assign([BaseSyntaxOP.createVar(var)], 
                                                        BaseSyntaxOP.createIdx(SYMBOL.XPATH_SYMBOL, "", [vid] + parent, BaseSyntaxOP.getValue(key)), 
                                                        lineno=0))
            self.FUNC_NODE.body.insert(idx+1, ast.Assign([BaseSyntaxOP.createVar(var2)], 
                                                        ast.Attribute(BaseSyntaxOP.createVar(var, False), "append", ast.Load()), 
                                                        lineno=0))
            idx += 2
            
            if MODE.TRYE_MODE:
                self.try_Make(idx, f"buffer_{self.ID}", node)
                
            self.FUNC_NODE.body.insert(idx, node)
            idx += 2
        
        self.FUNC_TYPE = "FOR" if temp else "GEN"
        
        return ast.Dict(nxkey, nxval)
    
    def struct_Make(self, idval, insidx, prule, srule, parent=None):
        """结构生成"""
        def __ext(infos, key, ext, flag=True):
            if "content" not in ext: return ext
            try:
                ext["content"] = ext["content"].format(VARID=self.ID)
            except Exception as e:
                pass
            
            vars = self.find_Var(infos)
            node = ast.parse(ext["content"]).body
            node = node if node else [ast.Expr(BaseSyntaxOP.createConst(ext["content"]))]
            
            for name, _ in ext["map"].items():
                if name not in vars: continue
                if name == key: continue
                if not flag: continue
            
                if not name.startswith(f"{SYMBOL.XPATH_SYMBOL}."):
                    ext["map"][name] = (SYMBOL.VAR_SYMBOL, "", [name])
            
            BaseSyntaxOP.tranfIFS(node, ext["map"])
            
            return node[0].value if isinstance(node[0], ast.Expr) else node[0]
        
        keys, vals = [], []
        for key, infos in prule.items():
            if key and key.startswith("__"): continue
            if key: keys.append(BaseSyntaxOP.blurring(key))
            if isinstance(infos, dict):
                nparent = (parent + [keys[-1]]) if parent else [keys[-1]]
                xkeys, xvals = self.struct_Make(idval, insidx, infos, srule, parent=nparent)
                vals.append(self.struct_Tranf(idval, [nparent] * len(xkeys), xkeys, xvals, insidx))
                continue
            
            if srule[key] == "dict":
                name, typehint, (owner, dtype, info), show, ext = infos
                
                if STRUC.SINGLE:
                    vals.append(BaseSyntaxOP.createIdx(owner, dtype, info))
                else:
                    if isinstance(info, (list, tuple)) and info and (info[0] == "data"):
                        vals.append(BaseSyntaxOP.createGenerate(self.find_Var([infos]), [["datas"]], "data", ("atom", [(1, name, typehint, (owner, dtype, __ext([infos], name, ext, False)) if ext else (owner, dtype, [info[:-1], info[-1]]), True)])))
                    elif (not info) and ext:
                        vals.append(BaseSyntaxOP.createGenerate(self.find_Var([infos]), [["datas"]], "data", ("atom", [(1, name, typehint, (owner, dtype, __ext([infos], name, ext, False)) if ext else (owner, dtype, [info[:-1], info[-1]]), True)])))
                    else:
                        vals.append(BaseSyntaxOP.createConst(info))
            elif srule[key] == "slist":
                name, typehint, (owner, dtype, info), show, ext = infos
                if isinstance(info, (list, tuple)) and info and (info[0] == "data"):
                    vals.append(BaseSyntaxOP.createGenerate(self.find_Var([infos]), [["datas"]], "data", ("atom", [(1, name, typehint, (owner, dtype, [info[:-1], info[-1]]), True)])))
                else:
                    vals.append(BaseSyntaxOP.createGenerate(self.find_Var([infos]), [["datas"]], "data", ("const", [(1, name, typehint, (owner, dtype, [info]), True)])))
            elif srule[key] == "dlist":
                 # 标记(0 常量, 1 变量取值, 2 原值)，取值
                temp = []
                
                for name, typehint, (owner, dtype, info), show, ext in infos:
                    if info and isinstance(info, (list, tuple)):
                        for i, val in enumerate(info):
                            if isinstance(val, dict):
                                info[i] = __ext(infos, name, val) if val else val
                    
                    ext = __ext(infos, name, ext) if ext else ext
                    
                    if isinstance(info, (list, tuple)) and info and info[0] == "data" and info[1:]:
                        if ext:
                            temp.append((2, name, typehint, (owner, dtype, ext), show))
                        else:
                            temp.append((1, name, typehint, (owner, dtype, [info[:-1], info[-1]]), show))
                    else:
                        if ext:
                            if owner == SYMBOL.VAR_SYMBOL:
                                temp.append((0, name, typehint, (owner, dtype, ext), show))
                            else:
                                temp.append((0, name, typehint, (owner, dtype, ext), show))
                        else:
                            if owner == SYMBOL.VAR_SYMBOL:
                                temp.append((1, name, typehint, (owner, dtype, [info[:-1], info[-1]]), show))
                            else:
                                temp.append((0, name, typehint, (owner, dtype, info), show))

                if STRUC.SINGLE:
                    vals.append(BaseSyntaxOP.createGenerate(self.find_Var(infos), None, "data", (STRUC.PROTOCOL, temp)))
                else:
                    vals.append(BaseSyntaxOP.createGenerate(self.find_Var(infos), [["datas"]], "data", (STRUC.PROTOCOL, temp)))

        return keys, vals
    
    def try_Make(self, idx, idval, node):
        """TRY 保护生成"""
        trynode = ast.Try(node.body, [ast.ExceptHandler(BaseSyntaxOP.createVar("Exception", False),
                                                        "e",
                                                        [ast.Expr(ast.Call(BaseSyntaxOP.createVar(f"{idval}_try_append", False),
                                                                           [BaseSyntaxOP.createVar("data", False)],
                                                                           []))])],
                          [], [])
        node.body = [trynode]
        
    def type_Hint_Make(self):
        """类型信息提取"""
        def __call(params):
            if not params: return 
            
            if isinstance(params, dict):
                for key, doc in params.items():
                    __call(doc)
            elif isinstance(params, list):
                params = params if params and isinstance(params[0], (list, tuple)) else [params]
                
                for name, typehint, (owner, dtype, ext), *_ in params:
                    if not typehint: continue
                    self.TYPE_HINTS[name] = typehint
        
                    if (owner == SYMBOL.VAR_SYMBOL) and len(ext) == 1:
                        self.TYPE_HINTS[ext[-1]] = typehint
        
        for params in self.PARAM_RULE:
            __call(params)
    
    def visit_Assign(self, node):
        try:
            idVal = node.targets[0].id
        except Exception as e:
            return node
        
        if idVal == "datas":
            if getattr(node.value, "value", True) == None:
                owner, dtype, dataRule = self.DATAS_RULE
                
                if owner == SYMBOL.XPATH_SYMBOL:
                    if dataRule[0] == "datas":
                        if len(dataRule) == 1:
                            node.value = BaseSyntaxOP.createIdx(owner, dtype, dataRule)
                        else:
                            node.value = BaseSyntaxOP.createIdx(owner, dtype, dataRule[:-1], dataRule[-1])
                    else:
                        node.value = BaseSyntaxOP.createConst(dataRule)
                elif owner == SYMBOL.FUNC_SYMBOL:
                    node.value = ast.parse(dataRule).body[0].value
                else:
                    raise ERROR.InputError("The input should be an XPATH !")
        elif idVal == f"filter_{self.ID}":
            if self.FILTER_RULE:
                filterExt = ast.parse(self.FILTER_RULE["content"])
                BaseSyntaxOP.tranfIFS(filterExt.body, self.FILTER_RULE["map"])
                
                node.value.generators[0].ifs = [filterExt.body[0].value]
        elif idVal == f"index_{self.ID}":
            node.value = ast.Dict([BaseSyntaxOP.createConst(key) for key in self.INDEX_RULE], 
                                  [ast.Dict([], []) for _ in self.INDEX_RULE])
        elif idVal == f"buffer_{self.ID}":
            insIdx = 0 # 插入位置
            tryinsIdx = None # try 插入位置
            for i, obj in enumerate(self.FUNC_NODE.body):
                if isinstance(obj, ast.Assign) and obj.targets[0].id == idVal:
                    insIdx = i + 1
                
            if len(self.PARAM_RULE) == 1 and (not self.PARAM_RULE[-1][self.PARAM_RULE[-1]["__parent__"]]):
                # 当没有数据处理描述时, 直接等于输入值
                node.value = BaseSyntaxOP.createVar("datas", False)
                return node
            
            keys, vals = [], []
            for i, param in enumerate(self.PARAM_RULE):
                xkey, xval = self.struct_Make(idVal, insIdx, self.PARAM_RULE[i], self.STRUC_RULE[i])
                keys.extend(xkey)
                vals.extend(xval)
            
            if keys and vals:
                node.value = self.struct_Tranf(idVal, [[x] for x in keys], keys, vals, insIdx)
                
                if STRUC.SINGLE:
                    subnode = BaseSyntaxOP.createGenerate([], [["datas"]], "data", (STRUC.PROTOCOL, node.value))
                    
                    if MODE.TRYE_MODE:
                        node.value = ast.List([], ast.Load())
                        self.FUNC_NODE.body.insert(insIdx, ast.Assign([BaseSyntaxOP.createVar(f"{idVal}_append")], 
                                                                     ast.Attribute(BaseSyntaxOP.createVar(idVal, False), "append", ast.Load()), 
                                                                     lineno=0))
                        subnode.body[-1].value.func = BaseSyntaxOP.createVar(f"{idVal}_append", False)
                        
                        self.try_Make(insIdx, idVal, subnode)
                        self.FUNC_NODE.body.insert(insIdx + 1, subnode)
                        tryinsIdx = insIdx + 1
                    else:
                        node.value = subnode
                else:
                    tryinsIdx = insIdx
            else:
                if isinstance(vals[-1], ast.For):
                    node.value = ast.List([], ast.Load())
                    self.FUNC_NODE.body.insert(insIdx, ast.Assign([BaseSyntaxOP.createVar(f"{idVal}_append")], 
                                                            ast.Attribute(BaseSyntaxOP.createVar(idVal, False), "append", ast.Load()), 
                                                            lineno=0))
                    vals[-1].body[-1].value.func = BaseSyntaxOP.createVar(f"{idVal}_append", False)
                    
                    insIdx += 1
                    
                    if self.INDEX_RULE:
                        subnode = ast.Assign([BaseSyntaxOP.createVar(f"temp_{self.ID}")], 
                                              vals[-1].body[-1].value.args[0],
                                              lineno=0)
                        vals[-1].body.pop()
                        vals[-1].body.append(subnode)
                        
                        for name, callback in self.INDEX_RULE.items():
                            temp = BaseSyntaxOP.createIdx("", "", [(f"index_{self.ID}", 1), (name, 0)], [name], True, safe=False)
                            if callback:
                                subnode = ast.Assign([temp], 
                                                      ast.parse(f"{callback}({name}, index_{self.ID}['{name}'].get({name}) ,temp_{self.ID})").body[0].value, 
                                                      lineno=0)
                            else:
                                subnode = ast.Assign([temp], 
                                                      BaseSyntaxOP.createVar(f"temp_{self.ID}", False), 
                                                      lineno=0)
                                
                            vals[-1].body.append(subnode)
                    
                    if MODE.TRYE_MODE:
                        self.try_Make(insIdx, idVal, vals[-1])
                        
                    self.FUNC_NODE.body.insert(insIdx, vals[-1])
                    tryinsIdx = insIdx
                    
                    self.FUNC_TYPE = "FOR"
                else:
                    node.value = vals[-1]
                    
                    if STRUC.SINGLE:
                        node.value = BaseSyntaxOP.createGenerate([], [["datas"]], "data", (STRUC.PROTOCOL, node.value))
                    
                    self.FUNC_TYPE = "GEN"
                
            if MODE.TRYE_MODE and (tryinsIdx != None):
                name = f"{idVal}_try"
                
                self.FUNC_NODE.body[-1].value = ast.Tuple([self.FUNC_NODE.body[-1].value,
                                                          BaseSyntaxOP.createVar(name, False)], ast.Load())
                self.FUNC_NODE.body.insert(tryinsIdx, ast.Assign([BaseSyntaxOP.createVar(name)],
                                                                ast.List([], ast.Load()),
                                                                lineno=0))
                self.FUNC_NODE.body.insert(tryinsIdx + 1, ast.Assign([BaseSyntaxOP.createVar(f"{name}_append")], 
                                                                    ast.Attribute(BaseSyntaxOP.createVar(f"{idVal}_try", False), "append", ast.Load()), 
                                                                    lineno=0))
        return node
    
    @classmethod
    def make(cls, env, hid, template, datarule, indexrule, paramrule, filterule, strucrule):
        """解析模板 → AST生成 → AST转换 → 字节码编译 → 缓存函数（DEBUG模式用纯Python，否则Cython加速）"""
        exprAst = ast.parse(template)
        
        ins = cls()
        
        ins.ID = hid
        ins.DATAS_RULE = datarule
        ins.INDEX_RULE = indexrule
        ins.PARAM_RULE = paramrule
        ins.FILTER_RULE = filterule
        ins.STRUC_RULE = strucrule
        ins.FUNC_NODE = exprAst.body[0]
        
        ins.type_Hint_Make()
        exprInLineAst = ins.visit(exprAst)
        ast.fix_missing_locations(exprInLineAst)
        
        try:
            if hasattr(ast, "unparse"):
                sourceCode = ast.unparse(exprInLineAst)
            else:
                sourceCode = astunparse.unparse(exprInLineAst)[2:]
        except Exception:
            sourceCode = None
        
        if MODE.DEBUG and sourceCode:
            lines = sourceCode.splitlines()
            for i, line in enumerate(lines, 1):
                print(f"{i:<4d} {line}")
            print("\n")
        
        if sourceCode:
            byteCode = compile(sourceCode, "<generated>", "exec")
        else:
            byteCode = compile(exprInLineAst, "<generated>", "exec")
        
        funcode = [c for c in byteCode.co_consts if isinstance(c, types.CodeType)][0]
        handler = types.FunctionType(funcode, env)
        funcType = ins.FUNC_TYPE
        
        if MODE.CYTHON_MODE and sourceCode:
            match = re.search(r"def\s+(_inline_call_\w+)\(datas", sourceCode)
            funcName = match.group(1) if match else None
            
            if funcName:
                try:
                    handler = PY2Cython.make(env, hid, sourceCode, f"{funcName}", ins.TYPE_HINTS)
                    funcType = "CYTHON"
                except Exception as e:
                    print(f"[WARN] Cython compilation failed, using pure Python. Reason: {e}")
        
        return funcType, handler
