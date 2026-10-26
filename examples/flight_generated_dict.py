def run(_d):
 _d=_d['index']
 _o=[]
 _c=_o.append
 for R in _d:
  _a=R[5]['a']
  _a1=_a[1]
  _a2=_a[2]
  _c([_a,_a1,_a2,R[2],str(_a1) + str(_a2),_a1 + _a2,_a2 + _a1,_a[2] + 123,_a[3][2] + _a[3][1]])
 return {'hour':[r[0]for r in _o],'hourA':[r[1]for r in _o],'hourB':[r[2]for r in _o],'hourC':[r[3]for r in _o],'hourD':[r[4]for r in _o],'hourE':[r[5]for r in _o],'hourF':[r[6]for r in _o],'hourG':[r[7]for r in _o],'hourH':[r[8]for r in _o]}
