#include <stddef.h>
#include <stdint.h>

#include "Python.h"
#include "datetime.h"

typedef struct {
    PyObject *utcoff;
    PyObject *dstoff;
    PyObject *tzname;
} _ttinfo;

typedef struct {
    _ttinfo std;
    _ttinfo dst;
    int dst_diff;
    PyObject *start;
    PyObject *end;
} _tzrule;

typedef struct {
    PyDateTime_TZInfo base;
    PyObject *key;
    PyObject *weakreflist;
    unsigned int num_transitions;
    unsigned int num_ttinfos;
    int64_t *trans_list_utc;
    int64_t *trans_list_wall[2];
    _ttinfo *ttinfos;
    _ttinfo ttinfo_before;
    _tzrule tzrule_after;
    unsigned char from_cache;
} PyZoneInfo_ZoneInfo;

// Constants
static PyObject *ZONEINFO_WEAK_CACHE = NULL;

static PyObject *
zoneinfo_new_instance(PyTypeObject *type, char *key)
{
    PyObject *self = (PyObject *)(type->tp_alloc(type, 0));
    if (self == NULL) {
        return NULL;
    }

    return self;
}

static PyObject *
zoneinfo_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    // TODO: Support subclasses
    PyObject *key = NULL;
    static char *kwlist[] = {"key", NULL};
    if (PyArg_ParseTupleAndKeywords(args, kw, "O", kwlist, &key) == 0) {
        return NULL;
    }

    assert(ZONEINFO_WEAK_CACHE != NULL);
    PyObject *instance =
        PyObject_CallMethod(ZONEINFO_WEAK_CACHE, "get", "O", key, Py_None);
    if (instance == NULL) {
        return NULL;
    }

    if (instance == Py_None) {
        PyObject *tmp = zoneinfo_new_instance(type, key);
        if (tmp == NULL) {
            return NULL;
        }

        instance = PyObject_CallMethod(ZONEINFO_WEAK_CACHE, "setdefault", "OO",
                                       key, tmp);
        ((PyZoneInfo_ZoneInfo *)instance)->from_cache = 1;

        Py_DECREF(tmp);

        if (instance == NULL) {
            return NULL;
        }
    }

    // TODO: Add strong cache
    return instance;
}

static PyObject *
zoneinfo_clear_cache(PyObject *self)
{
    PyObject_CallMethod(ZONEINFO_WEAK_CACHE, "clear", NULL);
    Py_RETURN_NONE;
}

static PyMethodDef zoneinfo_methods[] = {
    {"clear_cache", (PyCFunction)zoneinfo_clear_cache,
     METH_NOARGS | METH_CLASS, PyDoc_STR("Clear the ZoneInfo cache.")},
    {NULL} /* Sentinel */
};

static PyTypeObject PyZoneInfo_ZoneInfoType = {
    PyVarObject_HEAD_INIT(NULL, 0).tp_name = "zoneinfo._czoneinfo.ZoneInfo",
    .tp_basicsize = sizeof(PyZoneInfo_ZoneInfo),
    .tp_weaklistoffset = offsetof(PyZoneInfo_ZoneInfo, weakreflist),
    /* .tp_repr = zoneinfo_repr, */
    /* .tp_str = zoneinfo_str, */
    .tp_getattro = PyObject_GenericGetAttr,
    .tp_flags = (Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE),
    /* .tp_doc = zoneinfo_doc, */
    .tp_methods = zoneinfo_methods,
    .tp_new = zoneinfo_new,
};

static PyMethodDef module_methods[] = {{NULL, NULL}};
static void
module_free()
{
    // Clear caches
    Py_XDECREF(ZONEINFO_WEAK_CACHE);
    ZONEINFO_WEAK_CACHE = NULL;
}

static struct PyModuleDef zoneinfomodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "zoneinfo._czoneinfo",
    .m_doc = "C implementation of the zoneinfo module",
    .m_size = -1,
    .m_methods = module_methods,
    .m_free = (freefunc)module_free};

PyMODINIT_FUNC
PyInit__czoneinfo(void)
{
    PyObject *m; /* a module object */
    m = PyModule_Create(&zoneinfomodule);

    if (m == NULL) {
        return NULL;
    }

    PyDateTime_IMPORT;
    PyZoneInfo_ZoneInfoType.tp_base = PyDateTimeAPI->TZInfoType;
    if (PyType_Ready(&PyZoneInfo_ZoneInfoType) < 0) {
        goto error;
    }

    Py_INCREF(&PyZoneInfo_ZoneInfoType);
    PyModule_AddObject(m, "ZoneInfo", (PyObject *)&PyZoneInfo_ZoneInfoType);

    /* Initialize caches */
    PyObject *weakref_module = NULL;
    PyObject *WeakValueDictionary = NULL;

    if ((weakref_module = PyImport_ImportModule("weakref")) == NULL) {
        goto error;
    }

    WeakValueDictionary =
        PyObject_GetAttrString(weakref_module, "WeakValueDictionary");
    Py_DECREF(weakref_module);
    weakref_module = NULL;
    if (WeakValueDictionary == NULL) {
        goto error;
    }
    PyObject *no_args = PyTuple_New(0);
    if (no_args == NULL) {
        Py_DECREF(WeakValueDictionary);
        goto error;
    }

    // TODO: VectorCall
    ZONEINFO_WEAK_CACHE = PyObject_CallObject(WeakValueDictionary, no_args);

    Py_DECREF(no_args);
    Py_DECREF(WeakValueDictionary);
    if (ZONEINFO_WEAK_CACHE == NULL) {
        goto error;
    }

    return m;

error:
    Py_DECREF(m);
    return NULL;
}
