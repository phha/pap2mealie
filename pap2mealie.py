#!env python3

from contextlib import suppress
import json
import logging as log
from types import SimpleNamespace
import click
import requests
from requests_toolbelt.sessions import BaseUrlSession
from requests_toolbelt.multipart.encoder import MultipartEncoder
from dataclasses import dataclass
from zipfile import ZipFile
from gzip import GzipFile
from io import BytesIO
from itertools import islice
from base64 import b64decode

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

    def post_recipe(self, recipe):
        """Create a new recipe from JSON"""
        url = f"{self.base}recipes/create"
        return self.s.post(url, json=recipe)

    def put_image(self, slug, image, extension):
        """Upload a new image for a given recipe"""
        data = MultipartEncoder(
            fields={
                'image': ('image.jpg', image, 'image/jpeg'),
                'extension': extension}
        )
        return self.s.put(
            f"recipes/{slug}/image",
            data=data,
            headers={'Content-Type': data.content_type}
        )

    def post_image(self, slug, url):
        """Scrape an image for a given recipe"""
        return self.s.post(
            f"recipes({slug}/image",
            json={'url': url},
        )


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
    '--log',
    type=click.Path(writable=True, dir_okay=False),
    default='pap2mealie.log',
    help='Path to the log file.')
def pap2mealie(file, url, username, password, log):
    log.basicConfig(level=log.INFO, filename=log)
    ok = 0
    nok = 0
    api = Api(f"{url}/api/", username, password)
    with click.progressbar(
        paprika_recipes(file),
        length=paprika_recipes_count(file),
        label='Importing recipes'
    ) as bar:
        for recipe in bar:
            res = api.post_recipe(convert_recipe(recipe))
            if(res.ok):
                log.info(f"Successfully imported recipe '{recipe['name']}'")
                slug = res.text[1:-1]
                # Upload the (low quality) image from the export
                with(suppress(TypeError)):
                    image = BytesIO(b64decode(recipe['photo_data']))
                    api.put_image(slug, image, 'jpg')
                # Try to re-scrape and overwrite the low-quality image with
                # a better image.
                image_url = recipe['image_url']
                api.post_image(slug, image_url)
            else:
                nok += 1
                log.error(f"Error while importing recipe '{recipe['name']}'")
                log.error(f"Code: {res.status_code}")
                log.error(f"{res.content}")
    click.echo(f"Imported: {ok}")
    click.echo(f"Errors: {nok}")
    click.echo('See log for details.')

if __name__ == '__main__':
    pap2mealie()
