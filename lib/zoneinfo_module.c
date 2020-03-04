#include "Python.h"
#include "datetime.h"

static PyMethodDef module_methods[] = {{NULL, NULL}};

static struct PyModuleDef zoneinfomodule = {
    PyModuleDef_HEAD_INIT,
    "zoneinfo._czoneinfo",
    "C implementation of the zoneinfo module",
    -1,
    module_methods,
    NULL,
    NULL,
    NULL,
    NULL};

PyMODINIT_FUNC
PyInit__czoneinfo(void)
{
    PyObject *m; /* a module object */
    m = PyModule_Create(&zoneinfomodule);

    if (m == NULL) {
        return NULL;
    }

    return m;
}
