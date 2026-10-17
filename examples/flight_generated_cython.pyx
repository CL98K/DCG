def run(_d):
 cdef object _o
 cdef object R
 cdef object _a
 cdef object _a1
 cdef object _a2
 _d=_d['index']
 _o=[]
 for R in _d:
  _a=R[5]['a']
  _a1=_a[1]
  _a2=_a[2]
  _o.append([_a,_a1,_a2,R[2],str(_a1) + str(_a2),_a1 + _a2,_a2 + _a1,_a[2] + 123,_a[3][2] + _a[3][1]])
 return _o
