#include <stddef.h>
#include <stdint.h>

#include "Python.h"
#include "datetime.h"

// Imports
PyObject *io_open = NULL;
PyObject *_tzpath_find_tzfile = NULL;
PyObject *_common_mod = NULL;

typedef struct {
    PyObject *utcoff;
    PyObject *dstoff;
    PyObject *tzname;
    long utcoff_seconds;
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
    _ttinfo **trans_ttinfos;  // References to the ttinfo for each transition
    _ttinfo *ttinfo_before;
    _tzrule tzrule_after;
    _ttinfo *_ttinfos;  // Unique array of ttinfos for ease of deallocation
    unsigned char from_cache;
} PyZoneInfo_ZoneInfo;

// Constants
static PyObject *ZONEINFO_WEAK_CACHE = NULL;
static PyObject *TIMEDELTA_CACHE = NULL;

// Forward declarations
static int
load_data(PyZoneInfo_ZoneInfo *self, PyObject *file_obj);
static void
utcoff_to_dstoff(size_t *trans_idx, long *utcoffs, long *dstoffs,
                 unsigned char *isdsts, size_t num_transitions,
                 size_t num_ttinfos);
static int
ts_to_local(size_t *trans_idx, int64_t *trans_utc, long *utcoff,
            int64_t *trans_local[2], size_t num_transitions);
static int
build_ttinfo(long utcoffset, long dstoffset, PyObject *tzname, _ttinfo *out);
static void
xdecref_ttinfo(_ttinfo *ttinfo);
static PyObject *
load_timedelta(long seconds);

static PyObject *
zoneinfo_new_instance(PyTypeObject *type, PyObject *key)
{
    PyObject *file_obj = NULL;
    PyObject *file_path = NULL;

    file_path = PyObject_CallFunctionObjArgs(_tzpath_find_tzfile, key, NULL);
    if (file_path == NULL) {
        return NULL;
    }
    else if (file_path == Py_None) {
        file_obj = PyObject_CallMethod(_common_mod, "load_tzdata", "O", key);
        if (file_obj == NULL) {
            return NULL;
        }
    }

    PyObject *self = (PyObject *)(type->tp_alloc(type, 0));
    if (self == NULL) {
        goto error;
    }

    if (file_obj == NULL) {
        file_obj = PyObject_CallFunction(io_open, "Os", file_path, "rb");
        if (file_obj == NULL) {
            goto error;
        }
    }

    if (load_data((PyZoneInfo_ZoneInfo *)self, file_obj)) {
        goto error;
    }

    PyObject *rv = PyObject_CallMethod(file_obj, "close", NULL);
    Py_DECREF(file_obj);
    file_obj = NULL;
    if (rv == NULL) {
        goto error;
    }
    Py_DECREF(rv);

    ((PyZoneInfo_ZoneInfo *)self)->key = key;
    Py_INCREF(key);

    goto cleanup;
error:
    Py_XDECREF(self);
    self = NULL;
cleanup:
    if (file_obj != NULL) {
        PyObject_CallMethod(file_obj, "close", NULL);
        Py_DECREF(file_obj);
    }
    Py_DECREF(file_path);
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

static void
zoneinfo_dealloc(PyObject *obj_self)
{
    PyZoneInfo_ZoneInfo *self = (PyZoneInfo_ZoneInfo *)obj_self;

    if (self->weakreflist != NULL) {
        PyObject_ClearWeakRefs(obj_self);
    }

    if (self->trans_list_utc != NULL) {
        PyMem_Free(self->trans_list_utc);
    }

    for (size_t i = 0; i < 2; i++) {
        if (self->trans_list_wall[i] != NULL) {
            PyMem_Free(self->trans_list_wall[i]);
        }
    }

    if (self->_ttinfos != NULL) {
        for (size_t i = 0; i < self->num_ttinfos; ++i) {
            xdecref_ttinfo(&(self->_ttinfos[i]));
        }
        PyMem_Free(self->_ttinfos);
    }

    if (self->trans_ttinfos != NULL) {
        PyMem_Free(self->trans_ttinfos);
    }

    Py_XDECREF(self->key);
}

static PyObject *
zoneinfo_nocache(PyTypeObject *cls, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"key", NULL};
    PyObject *key = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O", kwlist, &key)) {
        return NULL;
    }

    PyObject *out = zoneinfo_new_instance(cls, key);
    if (out != NULL) {
        ((PyZoneInfo_ZoneInfo *)out)->from_cache = 0;
    }

    return out;
}

static PyObject *
zoneinfo_clear_cache(PyObject *self)
{
    PyObject_CallMethod(ZONEINFO_WEAK_CACHE, "clear", NULL);
    Py_RETURN_NONE;
}

/* It is relatively expensive to construct new timedelta objects, and in most
 * cases we're looking at a relatively small number of timedeltas, such as
 * integer number of hours, etc. We will keep a cache so that we construct
 * a minimal number of these.
 *
 * Possibly this should be replaced with an LRU cache so that it's not possible
 * for the memory usage to explode from this, but in order for this to be a
 * serious problem, one would need to deliberately craft a malicious time zone
 * file with many distinct offsets. As of tzdb 2019c, loading every single zone
 * fills the cache with ~450 timedeltas for a total size of ~12kB.
 *
 * This returns a new reference to the timedelta.
 */
static PyObject *
load_timedelta(long seconds)
{
    PyObject *rv = NULL;
    PyObject *pyoffset = PyLong_FromLong(seconds);
    if (pyoffset == NULL) {
        return NULL;
    }
    int contains = PyDict_Contains(TIMEDELTA_CACHE, pyoffset);
    if (contains == -1) {
        goto error;
    }

    if (!contains) {
        PyObject *tmp = PyDateTimeAPI->Delta_FromDelta(
            0, seconds, 0, 1, PyDateTimeAPI->DeltaType);

        if (tmp == NULL) {
            goto error;
        }

        rv = PyDict_SetDefault(TIMEDELTA_CACHE, pyoffset, tmp);
        Py_DECREF(tmp);
    }
    else {
        rv = PyDict_GetItem(TIMEDELTA_CACHE, pyoffset);
    }

    Py_DECREF(pyoffset);
    Py_INCREF(rv);
    return rv;
error:
    Py_DECREF(pyoffset);
    return NULL;
}

/* Constructor for _ttinfo object - this starts by initializing the _ttinfo
 * to { NULL, NULL, NULL }, so that Py_XDECREF will work on partially
 * initialized _ttinfo objects.
 */
static int
build_ttinfo(long utcoffset, long dstoffset, PyObject *tzname, _ttinfo *out)
{
    out->utcoff = NULL;
    out->dstoff = NULL;
    out->tzname = NULL;

    out->utcoff_seconds = utcoffset;
    out->utcoff = load_timedelta(utcoffset);
    if (out->utcoff == NULL) {
        return -1;
    }

    out->dstoff = load_timedelta(dstoffset);
    if (out->dstoff == NULL) {
        return -1;
    }

    out->tzname = tzname;
    Py_INCREF(tzname);

    return 0;
}

/* Decrease reference count on any non-NULL members of a _ttinfo  */
static void
xdecref_ttinfo(_ttinfo *ttinfo)
{
    if (ttinfo != NULL) {
        Py_XDECREF(ttinfo->utcoff);
        Py_XDECREF(ttinfo->dstoff);
        Py_XDECREF(ttinfo->tzname);
    }
}

/* Given a file-like object, this populates a ZoneInfo object
 *
 * The current version calls into a Python function to read the data from
 * file into Python objects, and this translates those Python objects into
 * C values and calculates derived values (e.g. dstoff) in C.
 *
 * This returns 0 on success and -1 on failure.
 *
 * The function will never return while `self` is partially initialized —
 * the object only needs to be freed / deallocated if this succeeds.
 */
static int
load_data(PyZoneInfo_ZoneInfo *self, PyObject *file_obj)
{
    PyObject *data_tuple = NULL;

    long *utcoff = NULL;
    long *dstoff = NULL;
    size_t *trans_idx = NULL;
    unsigned char *isdst = NULL;

    self->trans_list_utc = NULL;
    self->trans_list_wall[0] = NULL;
    self->trans_list_wall[1] = NULL;
    self->trans_ttinfos = NULL;
    self->_ttinfos = NULL;

    size_t ttinfos_allocated = 0;

    data_tuple = PyObject_CallMethod(_common_mod, "load_data", "O", file_obj);
    if (data_tuple == NULL) {
        goto error;
    }

    // Unpack the data tuple
    PyObject *trans_idx_list = PyTuple_GetItem(data_tuple, 0);
    if (trans_idx_list == NULL) {
        goto error;
    }

    PyObject *trans_utc = PyTuple_GetItem(data_tuple, 1);
    if (trans_utc == NULL) {
        goto error;
    }

    PyObject *utcoff_list = PyTuple_GetItem(data_tuple, 2);
    if (utcoff_list == NULL) {
        goto error;
    }

    PyObject *isdst_list = PyTuple_GetItem(data_tuple, 3);
    if (isdst_list == NULL) {
        goto error;
    }

    PyObject *abbr = PyTuple_GetItem(data_tuple, 4);
    if (abbr == NULL) {
        goto error;
    }

    // Load the relevant sizes
    Py_ssize_t num_transitions = PyTuple_Size(trans_utc);
    if (num_transitions == -1) {
        goto error;
    }

    Py_ssize_t num_ttinfos = PyTuple_Size(utcoff_list);
    if (num_ttinfos == -1) {
        goto error;
    }

    self->num_transitions = (size_t)num_transitions;
    self->num_ttinfos = (size_t)num_ttinfos;

    // Load the transition indices and list
    self->trans_list_utc =
        PyMem_Malloc(self->num_transitions * sizeof(int64_t));
    trans_idx = PyMem_Malloc(self->num_transitions * sizeof(Py_ssize_t));

    for (Py_ssize_t i = 0; i < self->num_transitions; ++i) {
        PyObject *num = PyTuple_GetItem(trans_utc, i);
        if (num == NULL) {
            goto error;
        }
        self->trans_list_utc[i] = PyLong_AsLongLong(num);
        if (self->trans_list_utc[i] == -1 && PyErr_Occurred()) {
            goto error;
        }

        num = PyTuple_GetItem(trans_idx_list, i);
        if (num == NULL) {
            goto error;
        }

        Py_ssize_t cur_trans_idx = PyLong_AsSsize_t(num);
        if (cur_trans_idx == -1) {
            goto error;
        }

        trans_idx[i] = (size_t)cur_trans_idx;
        if (trans_idx[i] > self->num_ttinfos) {
            PyErr_Format(
                PyExc_ValueError,
                "Invalid transition index found while reading TZif: %zd",
                cur_trans_idx);

            goto error;
        }
    }

    // Load UTC offsets and isdst (size num_ttinfos)
    utcoff = PyMem_Malloc(self->num_ttinfos * sizeof(long));
    isdst = PyMem_Malloc(self->num_ttinfos * sizeof(unsigned char));

    if (utcoff == NULL || isdst == NULL) {
        goto error;
    }
    for (Py_ssize_t i = 0; i < self->num_ttinfos; ++i) {
        PyObject *num = PyTuple_GetItem(utcoff_list, i);
        if (num == NULL) {
            goto error;
        }

        utcoff[i] = PyLong_AsLong(num);
        if (utcoff[i] == -1 && PyErr_Occurred()) {
            goto error;
        }

        num = PyTuple_GetItem(isdst_list, i);
        if (num == NULL) {
            goto error;
        }

        isdst[i] = PyObject_IsTrue(num);
        if (isdst[i] == -1) {
            goto error;
        }
    }

    dstoff = PyMem_Calloc(self->num_ttinfos, sizeof(long));
    if (dstoff == NULL) {
        goto error;
    }

    // Derive dstoff and trans_list_wall from the information we've loaded
    utcoff_to_dstoff(trans_idx, utcoff, dstoff, isdst, self->num_transitions,
                     self->num_ttinfos);

    if (ts_to_local(trans_idx, self->trans_list_utc, utcoff,
                    self->trans_list_wall, self->num_transitions)) {
        goto error;
    }

    // Build _ttinfo objects from utcoff, dstoff and abbr
    self->_ttinfos = PyMem_Malloc(self->num_ttinfos * sizeof(_ttinfo));
    for (size_t i = 0; i < self->num_ttinfos; ++i) {
        PyObject *tzname = PyTuple_GetItem(abbr, i);
        if (tzname == NULL) {
            goto error;
        }

        ttinfos_allocated++;
        if (build_ttinfo(utcoff[i], dstoff[i], tzname, &(self->_ttinfos[i]))) {
            goto error;
        }
    }

    // Build our mapping from transition to the ttinfo that applies
    self->trans_ttinfos =
        PyMem_Calloc(self->num_transitions, sizeof(_ttinfo *));
    for (size_t i = 0; i < self->num_transitions; ++i) {
        size_t ttinfo_idx = trans_idx[i];
        assert(ttinfo_idx < self->num_ttinfos);
        self->trans_ttinfos[i] = &(self->_ttinfos[ttinfo_idx]);
    }

    // Set ttinfo_before to the first non-DST transition
    for (size_t i = 0; i < self->num_ttinfos; ++i) {
        if (!isdst[i]) {
            self->ttinfo_before = &(self->_ttinfos[i]);
            break;
        }
    }

    // If there are only DST ttinfos, pick the first one, if there are no
    // ttinfos at all, set ttinfo_before to NULL
    if (self->ttinfo_before == NULL && self->num_ttinfos > 0) {
        self->ttinfo_before = &(self->_ttinfos[0]);
    }

    int rv = 0;
    goto cleanup;
error:
    // These resources only need to be freed if we have failed, if we succeed
    // in initializing a PyZoneInfo_ZoneInfo object, we can rely on its dealloc
    // method to free the relevant resources.
    if (self->trans_list_utc != NULL) {
        PyMem_Free(self->trans_list_utc);
        self->trans_list_utc = NULL;
    }

    for (size_t i = 0; i < 2; ++i) {
        if (self->trans_list_wall[i] != NULL) {
            PyMem_Free(self->trans_list_wall[i]);
            self->trans_list_wall[i] = NULL;
        }
    }

    if (self->_ttinfos != NULL) {
        for (size_t i = 0; i < ttinfos_allocated; ++i) {
            xdecref_ttinfo(&(self->_ttinfos[i]));
        }
        PyMem_Free(self->_ttinfos);
        self->_ttinfos = NULL;
    }

    if (self->trans_ttinfos != NULL) {
        PyMem_Free(self->trans_ttinfos);
        self->trans_ttinfos = NULL;
    }

    rv = -1;
cleanup:
    Py_XDECREF(data_tuple);

    if (utcoff != NULL) {
        PyMem_Free(utcoff);
    }

    if (dstoff != NULL) {
        PyMem_Free(dstoff);
    }

    if (isdst != NULL) {
        PyMem_Free(isdst);
    }

    if (trans_idx != NULL) {
        PyMem_Free(trans_idx);
    }

    return rv;
}

/* Calculate DST offsets from transitions and UTC offsets
 *
 * This is necessary because each C `ttinfo` only contains the UTC offset,
 * time zone abbreviation and an isdst boolean - it does not include the
 * amount of the DST offset, but we need the amount for the dst() function.
 *
 * Thus function uses heuristics to infer what the offset should be, so it
 * is not guaranteed that this will work for all zones. If we cannot assign
 * a value for a given DST offset, we'll assume it's 1H rather than 0H, so
 * bool(dt.dst()) will always match ttinfo.isdst.
 */
static void
utcoff_to_dstoff(size_t *trans_idx, long *utcoffs, long *dstoffs,
                 unsigned char *isdsts, size_t num_transitions,
                 size_t num_ttinfos)
{
    size_t dst_count = 0;
    size_t dst_found = 0;
    for (size_t i = 0; i < num_ttinfos; ++i) {
        dst_count++;
    }

    for (size_t i = 1; i < num_transitions; ++i) {
        if (dst_count == dst_found) {
            break;
        }

        size_t idx = trans_idx[i];
        size_t comp_idx = trans_idx[i - 1];

        // Only look at DST offsets that have nto been assigned already
        if (!isdsts[idx] || dstoffs[idx] != 0) {
            continue;
        }

        long dstoff = 0;
        long utcoff = utcoffs[idx];

        if (!isdsts[comp_idx]) {
            dstoff = utcoff - utcoffs[comp_idx];
        }

        if (!dstoff && idx < (num_ttinfos - 1)) {
            comp_idx = trans_idx[i + 1];

            // If the following transition is also DST and we couldn't find
            // the DST offset by this point, we're going to have to skip it
            // and hope this transition gets assigned later
            if (isdsts[comp_idx]) {
                continue;
            }

            dstoff = utcoff - utcoffs[comp_idx];
        }

        if (dstoff) {
            dst_found++;
            dstoffs[idx] = dstoff;
        }
    }

    if (dst_found < dst_count) {
        // If there are time zones we didn't find a value for, we'll end up
        // with dstoff = 0 for something where isdst=1. This is obviously
        // wrong — one hour will be a much better guess than 0.
        for (size_t idx = 0; idx < num_ttinfos; ++idx) {
            if (isdsts[idx] && !dstoffs[idx]) {
                dstoffs[idx] = 3600;
            }
        }
    }
}

#define _swap(x, y, buffer) \
    buffer = x;             \
    x = y;                  \
    y = buffer;

/* Calculate transitions in local time from UTC time and offsets.
 *
 * We want to know when each transition occurs, denominated in the number of
 * nominal wall-time seconds between 1970-01-01T00:00:00 and the transition in
 * *local time* (note: this is *not* equivalent to the output of
 * datetime.timestamp, which is the total number of seconds actual elapsed
 * since 1970-01-01T00:00:00Z in UTC).
 *
 * This is an ambiguous question because "local time" can be ambiguous — but it
 * is disambiguated by the `fold` parameter, so we allocate two arrays:
 *
 *  trans_local[0]: The wall-time transitions for fold=0
 *  trans_local[1]: The wall-time transitions for fold=1
 *
 * This returns 0 on success and a negative number of failure. The trans_local
 * arrays must be freed if they are not NULL.
 */
static int
ts_to_local(size_t *trans_idx, int64_t *trans_utc, long *utcoff,
            int64_t *trans_local[2], size_t num_transitions)
{
    if (num_transitions == 0) {
        return 0;
    }

    // Copy the UTC transitions into each array to be modified in place later
    for (size_t i = 0; i < 2; ++i) {
        trans_local[i] = PyMem_Malloc(num_transitions * sizeof(int64_t));
        if (trans_local[i] == NULL) {
            return -1;
        }

        memcpy(trans_local[i], trans_utc, num_transitions * sizeof(int64_t));
    }

    int64_t offset_0, offset_1, buff;
    if (num_transitions > 1) {
        offset_0 = utcoff[0];
        offset_1 = utcoff[trans_idx[0]];

        if (offset_1 > offset_0) {
            _swap(offset_0, offset_1, buff);
        }
    }
    else {
        offset_0 = utcoff[0];
        offset_1 = utcoff[0];
    }

    trans_local[0][0] += offset_0;
    trans_local[1][0] += offset_1;

    for (size_t i = 1; i < num_transitions; ++i) {
        offset_0 = utcoff[trans_idx[i - 1]];
        offset_1 = utcoff[trans_idx[i]];

        if (offset_1 > offset_0) {
            _swap(offset_1, offset_0, buff);
        }

        trans_local[0][i] += offset_0;
        trans_local[1][i] += offset_1;
    }

    return 0;
}

/////
// Specify the ZoneInfo type
static PyMethodDef zoneinfo_methods[] = {
    {"clear_cache", (PyCFunction)zoneinfo_clear_cache,
     METH_NOARGS | METH_CLASS, PyDoc_STR("Clear the ZoneInfo cache.")},
    {"nocache", (PyCFunction)zoneinfo_nocache,
     METH_VARARGS | METH_KEYWORDS | METH_CLASS,
     PyDoc_STR("Get a new instance of ZoneInfo, bypassing the cache.")},
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
    .tp_dealloc = zoneinfo_dealloc,
};

/////
// Specify the zoneinfo._czoneinfo module
static PyMethodDef module_methods[] = {{NULL, NULL}};
static void
module_free()
{
    Py_XDECREF(TIMEDELTA_CACHE);
    TIMEDELTA_CACHE = NULL;

    Py_XDECREF(ZONEINFO_WEAK_CACHE);
    ZONEINFO_WEAK_CACHE = NULL;

    Py_XDECREF(_tzpath_find_tzfile);
    _tzpath_find_tzfile = NULL;

    Py_XDECREF(_common_mod);
    _common_mod = NULL;

    Py_XDECREF(io_open);
    io_open = NULL;
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

    /* Populate imports */
    PyObject *_tzpath_module = PyImport_ImportModule("zoneinfo._tzpath");
    if (_tzpath_module == NULL) {
        goto error;
    }

    _tzpath_find_tzfile =
        PyObject_GetAttrString(_tzpath_module, "find_tzfile");
    Py_DECREF(_tzpath_module);
    if (_tzpath_find_tzfile == NULL) {
        goto error;
    }

    PyObject *io_module = PyImport_ImportModule("io");
    if (io_module == NULL) {
        goto error;
    }

    io_open = PyObject_GetAttrString(io_module, "open");
    Py_DECREF(io_module);
    if (io_open == NULL) {
        goto error;
    }

    _common_mod = PyImport_ImportModule("zoneinfo._common");
    if (_common_mod == NULL) {
        goto error;
    }

    /* Initialize caches */
    TIMEDELTA_CACHE = PyDict_New();
    if (TIMEDELTA_CACHE == NULL) {
        goto error;
    }

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
