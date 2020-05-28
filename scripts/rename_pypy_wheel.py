import pathlib


def main():
    (generic_wheel,) = pathlib.Path("dist").glob("*.whl")

    name = generic_wheel.name
    old_comp = "-py3-none-any.whl"
    if not name.endswith(old_comp):
        raise ValueError(
            f"Unexpected wheel name, does not end with " + f"{old_comp}: {name}"
        )

    new_name = name[: -len(old_comp)] + "-pp3-none-any.whl"
    new_wheel = generic_wheel.parent / new_name

    generic_wheel.rename(new_wheel)
    print(f"Successfully renamed {generic_wheel} to {new_wheel}")


if __name__ == "__main__":
    main()
