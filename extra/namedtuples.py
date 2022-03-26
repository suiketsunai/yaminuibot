from collections import namedtuple

# main namedtuple for any links
Link = namedtuple(
    "Link",
    [
        "type",
        "link",
        "id",
    ],
)

# main namedtuple for artwork info
ArtWorkMedia = namedtuple(
    "ArtWorkMedia",
    [
        "link",
        "type",
        "id",
        "media",
        "user_id",
        "user",
        "username",
        "date",
        "desc",
        "links",
        "thumbs",
    ],
)
