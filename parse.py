import os

class Publication:

    def __init__(self, pubkey, info, file=''):
        """
        Creates an instance of a publication.

        :param pubkey: key to the publication
        :type: string
        :param info: BibTeX information
        :type: string
        :param file: file path
        :type: string
        """

        self._key = pubkey
        self._info = info
        self._fname = file
        if 'Url = {' in info:
            self._consolidated = True
        else:
            self._consolidated = False

    # --

    def get_key(self):
        """
        Get the publication key.

        :return: string (pubkey)
        """

        return self._key

    # --

    def get_info(self):
        """
        Get the publication BibTeX information.

        :return: string (info)
        """

        return self._info

    # --

    def file_empty(self):
        """
        Checks whether the publication has empty file info.

        :return: bool
        """

        return (self._fname == '') and (not self._consolidated)

    # --

    def assign_file(self, fname):
        """
        Add file information to publication.

        :param fname: file name to be added
        :type: string
        :return: bool (success)
        """

        if self._consolidated:
            print('Warning: Record already consolidated (ignoring).')
            return False

        self._fname = fname

        return True

    # --

    def consolidate(self):
        """
        Consolidates file information to BibTeX record.

        :return: boolean (success)
        """

        if self._consolidated:
            print('Warning: Record already consolidated (ignoring).')
            return False

        if self._fname != '':
            self._info = self._info[:-1]
            self._info += ',\n\t'
            self._info += 'url_Paper = {' + self._fname + '}}'

            self._consolidated = True

        return True

    # --

    def __str__(self):
        """
        Return representation of publication (BibTeX).

        :return: string (bibtex string)
        """

        return self._info

    # --

    def __repr__(self):
        """
        Return object representation of publication.

        :return: string (object rep)
        """

        s = 'Publication('
        s += self._key + ', ' + self._info

        if self._fname != '':
            s += ', ' + self._fname

        s += ')'

        return s

    # --

    def is_consolidated(self):
        """
        Returns whether current file is consolidated.

        :return: Boolean (consolidated)
        """

        return self._consolidated

    # --

    def __eq__(self, other):
        """
        Compare two records for same publication.

        :param other: Publication to compare to
        :type: Publication
        :return: boolean
        """

        if not isinstance(other, Publication):
            return False

        return self._key == other.get_key()

    # --

# --

def parse_bibfile(fname):
    """
    Parses a BibTeX file, building a list of references.

    :param fname: .bib file name
    :type: string
    :return: list (of publication records)
    """

    warnings = 0

    # - Check file extension

    if fname[-3:] != 'bib':
        warnings += 1
        print('Warning: Wrong extension encountered (extension mismatch ignored).')

    # - Open and read file

    f = open(fname, 'r')
    text = f.readlines()
    f.close()

    # - Process file

    pubs = {}

    curr_record = ''
    record_open = False

    for line in text:

        # - Parse current line

        # Comments and empty lines are ignored
        if line[0] == '%' or line in ('', '\n'):
            continue

        # Check if opening record
        elif line[0] == '@':
            if record_open:
                tokens = curr_record.split('{')
                key = tokens[1].split(',')[0]
                info = curr_record

                if key in pubs:
                    warnings += 1
                    print('Warning: Duplicate key found (old record overwritten).')

                pubs[key] = Publication(key, info)

                curr_record = line
            else:
                curr_record = line
                record_open = True

        # Other cases
        else:
            # No open record - ignore line
            if not record_open:
                warnings += 1
                print('Warning: Non-empty line ignored ("%s").' % line)
                continue
            else:
                curr_record += line

    if record_open:
        tokens = curr_record.split('{')
        key = tokens[1].split(',')[0]
        info = curr_record

        if key in pubs:
            warnings += 1
            print('Warning: Duplicate key found (old record overwritten).')

        pubs[key] = Publication(key, info)

    print('Finished parsing file %s.' % fname)
    print('%i warnings found.\n' % warnings)

    return pubs

# --

def load_file_info(folder, publist):
    """
    Adds file information to publication database.

    :param folder: Folder containing publication files (path from current dir)
    :type: string
    :param publist: Publication database
    :type: Dictionary
    :return: Dictionary (updated publication database)
    """

    files_all = os.listdir(folder)
    files_pdf = []

    warnings = 0

    for name in files_all:
        if name[-3:] == 'pdf':
            files_pdf += [name]

    for file in files_pdf:
        key = file[:-4]

        if key not in publist:
            warnings += 1
            print('Warning: Record for file %s not found (file ignored).' % file)
        else:
            full_path = folder + file

            if not publist[key].is_consolidated():
                success = publist[key].assign_file(full_path)

                if not success:
                    warnings += 1
                    print('Warning: Unable to assign file %s to record %s (ignored).' % (full_path, key))

                else:
                    success = publist[key].consolidate()
                    if not success:
                        warnings += 1
                        print('Warning: Unable to consolidate record %s (ignored).' % key)

            else:
                warnings += 1
                print('Warning: Record %s consolidated (ignored).' % key)

    print('Finished loading file info from %s.' % folder)
    print('%i warnings found.\n' % warnings)

    return publist

# --

def save_bib_file(publist, fname='biblio.bib'):
    """
    Saves a publication list as a bib file.

    :param pubs: Database of publications
    :type: Dictionary
    :param fname: Name of the bibfile to save the publications database
    :type: string
    """

    warnings = 0
    records  = 0
    f = open(fname, 'w')

    for pubkey in publist:
        if not publist[pubkey].is_consolidated():
            success = publist[pubkey].is_consolidated()
            if not success:
                warnings += 1
                print('Warning: Unable to consolidate record %s (writing unconsolidated).' % pubkey)

        try:
            records += 1
            print(publist[pubkey], file=f)
        except:
            print('Warning: Unable to write record %s (skipping record).' % pubkey)
            records -= 1

    f.close()

    print('Finished writing bib file.')
    print('Written %i records' % records)
    print('%i warnings found.' % warnings)

# --

if __name__ == '__main__':
    pubs = parse_bibfile('fmelo-nofiles.bib')
    load_file_info('publications/', pubs)
    save_bib_file(pubs, 'fmelo.bib')