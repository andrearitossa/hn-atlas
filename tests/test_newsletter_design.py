import unittest
from html.parser import HTMLParser
import newsletter

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href')

class NewsletterDesignTests(unittest.TestCase):
    def test_website_style_and_direct_source_links(self):
        posts=[dict(id=i,title=f'Story {i}',url=f'https://example.com/article/{i}') for i in range(1,8)]
        html=newsletter.render_html('Robotics',posts,1800000000,'https://example.com/unsubscribe',1185)
        self.assertEqual(newsletter.LIMIT,5)
        self.assertEqual(html.count('<h2 '),7)
        self.assertIn('max-width:800px',html)
        self.assertIn('Hacker <span',html)
        self.assertNotIn('>1.</td>',html)
        self.assertNotIn('A few good reads',html)
        self.assertNotIn('7 reads',html)
        self.assertNotIn('Georgia',html)
        self.assertIn('#fbfbfa',html)
        self.assertIn('#f56300',html)
        parser=Links();parser.feed(html)
        for post in posts:self.assertEqual(parser.links.count(post['url']),2)
        self.assertIn('https://hackeratlas.com/#/topic/1185',parser.links)

    def test_html_is_safe_readable_and_topic_specific(self):
        posts=[dict(id=1,title='<script>Bad & title</script>',url='javascript:alert(1)',summary='Useful <context>.',kind='Essay')]
        html=newsletter.render_html('Topic',posts,1800000000,'https://example.com/unsubscribe/token',1286)
        self.assertNotIn('<script>',html)
        self.assertNotIn('javascript:',html)
        self.assertIn('<h1',html)
        self.assertIn('Topic</h1>',html)
        self.assertNotIn('Geopolitics',html)
        self.assertNotIn('Something fun',html)
        self.assertIn('@media',html)
        self.assertNotIn('<img',html)
        parser=Links();parser.feed(html)
        self.assertIn('https://news.ycombinator.com/item?id=1',parser.links)
        self.assertIn('https://example.com/unsubscribe/token',parser.links)

    def test_uses_topic_name_and_renders_overview(self):
        post=dict(id=1,title='A discovery',url='https://example.com',summary='Old custom note')
        html=newsletter.render_html('Research',[post],1800000000,'https://example.com/unsubscribe',1136)
        self.assertIn('Research</h1>',html)
        self.assertNotIn('AI &amp; biotech',html)
        self.assertIn('Old custom note',html)

if __name__=='__main__':unittest.main()
