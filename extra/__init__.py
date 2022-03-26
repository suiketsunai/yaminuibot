# pixiv styles
class PixivStyle:
    styles = (
        IMAGE_LINK,
        IMAGE_INFO_LINK,
        IMAGE_INFO_EMBED_LINK,
        INFO_LINK,
        INFO_EMBED_LINK,
    ) = range(5)

    @classmethod
    def validate(cls, value: int):
        return value in cls.styles


# link types
class LinkType:
    types = (
        TWITTER,
        PIXIV,
    ) = range(2)

    @classmethod
    def validate(cls, value: int):
        return value in cls.types
