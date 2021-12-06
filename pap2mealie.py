#!env python3

from base64 import b64decode
from contextlib import suppress
from dataclasses import dataclass
from gzip import GzipFile
from io import BytesIO
from itertools import islice
import json
import logging as log
from types import SimpleNamespace
from zipfile import ZipFile

import click
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder
from requests_toolbelt.sessions import BaseUrlSession

def paprika_recipes_count(file):
    """Return the number of recipes inside the export file."""
    with ZipFile(file, 'r') as f:
        return len(f.namelist())

def paprika_recipes(file):
    """Yields all recipes inside the export file as JSON"""
    with ZipFile(file, 'r') as f:
        for name in f.namelist():
            with f.open(name, 'r') as inner_file:
                inner_data = BytesIO(inner_file.read())
                with GzipFile('r', fileobj=inner_data) as recipe_json:
                    recipe = json.load(recipe_json)
                    yield recipe

def convert_recipe(paprika):
    """Convert recipe data from Paprika's JSON to mealie's JSON"""
    p = SimpleNamespace(**paprika)
    return {
        'prepTime': p.prep_time,
        'recipeIngredient': p.ingredients.split('\n'),
        'notes': [{'title': '', 'text': p.notes}],
        'description': p.description,
        'orgURL': p.source_url,
        'performTime': p.cook_time,
        'totalTime': p.total_time,
        'recipe_yield': p.servings,
        'name': p.name,
        'rating': p.rating,
        'dateAdded': p.created[:p.created.find(' ')],
        'recipeCategory': p.categories,
        'recipeInstructions': [{'text': s} for s in p.directions.split('\n\n')],
        'tags': ['Paprika'],
        # 'extras': {
        #     'paprika_image_url': p.image_url,
        #     'difficulty': p.difficulty,
        #     'source': p.source,
            # 'paprika_uid': p.uid,
        # },
    }

@dataclass
class BearerAuth(requests.auth.AuthBase):
    """Authenticator for mealie API"""
    token: str

    def __call__(self, r):
        r.headers['Authorization'] = f"Bearer {self.token}"
        return r


@dataclass
class Api:
    """Mealie API"""
    base: str
    username: str
    password: str

    def __post_init__(self):
        # Create a session with our base URL
        self.s = BaseUrlSession(self.base)
        # Authenticate and register the token with our session
        credentials = {
            'username': self.username,
            'password': self.password
        }
        res = self.s.post('auth/token', data=credentials)
        self.s.auth = BearerAuth(res.json()['access_token'])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.s.close()

    def post_recipe(self, recipe):
        """Create a new recipe from JSON"""
        url = f"{self.base}recipes/create"
        res = self.s.post(url, json=recipe)
        if res.ok:
            log.info(f"Successfully imported recipe '{recipe['name']}'")
        else:
            log.error(f"Error while importing recipe '{recipe['name']}'")
            log.error(f"Code: {res.status_code}")
            log.error(f"{res.content}")
        return res


    def put_image(self, slug, image, extension):
        """Upload a new image for a given recipe"""
        data = MultipartEncoder(
            fields={
                'image': ('image.jpg', image, 'image/jpeg'),
                'extension': extension})
        res = self.s.put(
            f"recipes/{slug}/image",
            data=data,
            headers={'Content-Type': data.content_type})
        if res.ok:
            log.info(f"Successfully imported image for recipe recipe '{slug}'")
        else:
            log.warn(f"Error while importing image for recipe '{slug}'")
            log.warn(f"Code: {res.status_code}")
            log.warn(f"{res.content}")
        return res

    def post_image(self, slug, url):
        """Scrape an image for a given recipe"""
        res = self.s.post(
            f"recipes({slug}/image",
            json={'url': url})
        if res.ok:
            log.info(f"Successfully scraped image for recipe recipe '{slug}'")
        else:
            log.warn(f"Error while scraping image for recipe '{slug}' from '{url}'")
            log.warn(f"Code: {res.status_code}")
            log.warn(f"{res.content}")
        return res


    def import_paprika_recipe(self, recipe):
        """Import a recipe in Paprika's JSON Format to Mealie"""
        res = self.post_recipe(convert_recipe(recipe))
        if(res.ok):
            slug = res.text[1:-1]
            # Upload the (low quality) image from the export
            with(suppress(TypeError)):
                image = BytesIO(b64decode(recipe['photo_data']))
                self.put_image(slug, image, 'jpg')
            # Try to re-scrape and overwrite the low-quality image with
            # a better image.
            image_url = recipe['image_url']
            self.post_image(slug, image_url)
        return res.ok

@click.command()
@click.argument('file', type=click.File('rb'))
@click.argument('url')
@click.option(
    '--username',
    prompt=True,
    help='Username of the mealie user. Prompt if omitted')
@click.password_option(
    confirmation_prompt=False,
    help='Password of the mealie user. Prompt if omitted.')
@click.option(
    '--logfile',
    type=click.Path(writable=True, dir_okay=False),
    default='pap2mealie.log',
    help='Path to the log file.')
def pap2mealie(file, url, username, password, logfile):
    log.basicConfig(level=log.INFO, filename=logfile)
    ok = 0
    num_recipes = paprika_recipes_count(file)
    with Api(f"{url}/api/", username, password) as api:
        with click.progressbar(paprika_recipes(file),
            length=num_recipes,
            label='Importing recipes'
        ) as recipes:
            for recipe in recipes:
                if api.import_paprika_recipe(recipe):
                    ok += 1
    click.echo(f"Imported: {ok}")
    click.echo(f"Errors: {num_recipes - ok}")
    click.echo('See log for details.')

if __name__ == '__main__':
    pap2mealie()
