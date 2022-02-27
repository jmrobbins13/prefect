"""
Command line interface for managing storage settings
"""
import textwrap
from typing import List
from uuid import UUID

import pendulum
import pydantic
import typer
from fastapi import status
from httpx import HTTPStatusError
from rich.emoji import Emoji
from rich.pretty import Pretty
from rich.table import Table

import prefect
from prefect.blocks.core import get_block_class
from prefect.cli.base import (
    PrefectTyper,
    app,
    console,
    exit_with_error,
    exit_with_success,
)
from prefect.client import get_client

storage_config_app = PrefectTyper(
    name="storage",
    help="Commands for managing storage settings",
)
app.add_typer(storage_config_app)

JSON_TO_PY_TYPES = {"string": str}
JSON_TO_PY_EMPTY = {"string": "NOT-PROVIDED"}


@storage_config_app.command()
async def create():
    """Create a new storage configuration"""
    async with get_client() as client:
        specs = await client.read_block_specs("STORAGE")

    unconfigurable = set()
    for spec in specs:
        for property, property_spec in spec.fields["properties"].items():
            if (
                property_spec["type"] == "object"
                and property in spec.fields["required"]
            ):
                unconfigurable.add(spec)

    for spec in unconfigurable:
        specs.remove(spec)

    console.print("Found the following storage types:")
    for i, spec in enumerate(specs):
        console.print(f"{i}) {spec.name}")
        description = spec.fields["description"]
        if description:
            console.print(textwrap.indent(description, prefix="    "))

    selection = typer.prompt("Select a storage type to create", type=int)

    try:
        spec = specs[selection]
    except:
        exit_with_error(f"Invalid selection {selection!r}")

    property_specs = spec.fields["properties"]
    console.print(
        f"You've selected {spec.name}. It has {len(property_specs)} option(s). "
    )

    properties = {}
    required_properties = spec.fields.get("required", property_specs.keys())
    for property, property_spec in property_specs.items():
        required = property in required_properties
        optional = " (optional)" if not required else ""

        if property_spec["type"] == "object":
            # TODO: Look into handling arbitrary types better or avoid having arbitrary
            #       types in storage blocks
            continue

        # TODO: Some fields may have a default we can use instead
        not_provided_value = JSON_TO_PY_EMPTY[property_spec["type"]]
        default = not_provided_value if not required else None

        value = typer.prompt(
            f"{property_spec['title'].upper()}{optional}",
            type=JSON_TO_PY_TYPES[property_spec["type"]],
            default=default,
            show_default=default
            is not not_provided_value,  # Do not show our internal indicator
        )

        if value is not not_provided_value:
            properties[property] = value

    name = typer.prompt("Choose a name for this storage configuration")

    block_cls = get_block_class(spec.name, spec.version)

    console.print("Validating configuration...")
    try:
        block = block_cls(**properties)
    except Exception as exc:
        exit_with_error(f"Validation failed! {str(exc)}")

    console.print("Registering storage with server...")
    block_id = None
    while not block_id:
        async with get_client() as client:
            try:
                block_id = await client.create_block(
                    block=block, block_spec_id=spec.id, name=name
                )
            except HTTPStatusError as exc:
                if exc.response.status_code == status.HTTP_409_CONFLICT:
                    console.print(f"[red]The name {name!r} is already taken.[/]")
                    name = typer.prompt(
                        "Choose a new name for this storage configuration"
                    )
                else:
                    raise

    console.print(
        f"[green]Registered storage {name!r} with identifier '{block_id}'.[/]"
    )

    async with get_client() as client:
        if not await client.get_default_storage_block(as_json=True):
            set_default = typer.confirm(
                "You do not have a default storage you like to set this as your default storage?",
                default=True,
            )

            if set_default:
                await client.set_default_storage_block(block_id)
                exit_with_success(f"Set default storage to {name!r}.")

            else:
                console.print(
                    "Default left unchanged. Use `prefect storage set-default "
                    f"{block_id}` to set this as the default storage at a later time."
                )


@storage_config_app.command()
async def set_default(storage_block_id: UUID):
    """Change the default storage option"""
    async with get_client() as client:
        await client.set_default_storage_block(storage_block_id)
    exit_with_success("Updated default storage!")


@storage_config_app.command()
async def reset_default():
    """Reset the default storage option"""
    async with get_client() as client:
        await client.clear_default_storage_block()
    exit_with_success("Cleared default storage!")


@storage_config_app.command()
async def ls():
    """View configured storage options"""

    table = Table(title="Configured Storage")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Storage Type", style="green")
    table.add_column("Storage Version", style="green")
    table.add_column("Name", style="green")

    async with get_client() as client:
        json_blocks = await client.read_blocks(block_spec_type="STORAGE", as_json=True)
        default_storage_block = (
            await client.get_default_storage_block(as_json=True) or {}
        )
    blocks = pydantic.parse_obj_as(List[prefect.orion.schemas.core.Block], json_blocks)

    for block in blocks:
        table.add_row(
            str(block.id),
            block.block_spec.name,
            block.block_spec.version,
            (
                f"{block.name} [blue](**)[/]"
                if str(block.id) == str(default_storage_block.get("id"))
                else block.name
            ),
        )

    if not default_storage_block:
        table.caption = (
            "No default storage is set. Temporary local storage will be used."
            "\nSet a default with `prefect storage set-default <id>`"
        )
    else:
        table.caption = "(**) denotes the current default"

    console.print(table)
